"""Reject internal provenance in speech; preserve text rather than silently rewriting it."""

import re
import unicodedata

from .interface import ModelError

INTERNAL_MARKER = re.compile(
    r"[\[(【]\s*(?:参考|参见|引用|来源|依据|事件|对话|记录|ref(?:erence)?|source|receipt|event|seq)\s*(?:[:#]|第)?\s*(?:\d+|[\w-]+:\d+)"
    r"|\b(?:receipt_id|source_receipt_ids?|source_event_id|event_id|message_id|seq)\s*[:=]"
    r"|\b(?:world_[a-z0-9]+:\d+|message_[a-f0-9]{8,})\b"
    r"|\[\s*\d{1,8}\s*\]"
    r"|(?:参考|参见|引用)\s*(?:第)?\s*\d+(?=[\s)\]。!?！？,，;；]|$)"
    r"|(?<![A-Za-z0-9_])(?:receipt_id|source_receipt_ids?|source_event_id|event_id|message_id)(?![A-Za-z0-9_])",
    re.IGNORECASE,
)


def validate_speech_text(text: str) -> None:
    if INTERNAL_MARKER.search(unicodedata.normalize("NFKC", text)):
        raise ModelError(
            "SPEECH_INTERNAL_REFERENCE",
            "Character speech cannot contain internal source markers or citation numbers.",
            details={"reason": "INTERNAL_REFERENCE"},
        )


def validate_character_speech(content: dict, context: dict, stage: str) -> None:
    text = content.get("speech")
    if not isinstance(text, str):
        return
    validate_speech_text(text)
    for receipt in context.get("receipts", []):
        for key in ("receipt_id", "source_event_id"):
            reference = receipt.get(key)
            if isinstance(reference, str) and len(reference) >= 4 and reference in text:
                raise ModelError(
                    "SPEECH_INTERNAL_REFERENCE",
                    "Internal source IDs cannot be spoken.",
                    details={"reason": "INTERNAL_REFERENCE"},
                )
    if stage != "ordinary":
        return

    def normalized(value: str) -> str:
        return "".join(c for c in unicodedata.normalize("NFKC", value).casefold() if c.isalnum())

    current = normalized(text)
    previous = context.get("dialogue_recall", {}).get("last_own_utterances", [])
    if len(current) >= 20 and any(current == normalized(t) for t in previous if isinstance(t, str)):
        raise ModelError(
            "SPEECH_REPETITIVE",
            "A long ordinary speech repeats a recent own utterance verbatim.",
            details={"reason": "REPETITIVE_SPEECH"},
        )
