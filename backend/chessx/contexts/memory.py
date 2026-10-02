"""Owner-only, deterministic archive retrieval; summary is never the whole memory."""

import math
import re
from collections import Counter
from copy import deepcopy

from .agency import receipt_event
from .projections import LANGUAGE_FIELDS, STRUCTURE_FIELDS

NOISE = {
    "ActionChosen",
    "TurnStarted",
    "TurnCompleted",
    "RestApplied",
    "PhaseChanged",
    "CursorAdvanced",
    "PrimarySkipped",
    "ConversationPassed",
    "SettlementPermissionPassed",
    "MemoryUpdated",
    "GoalUpdated",
    "MemoryCompactionAdvanced",
}
HIGH_SIGNAL = re.compile(
    r"去世|死亡|死讯|身世|出生|承认|证据|答应|承诺|交给|秘密|位置|北边|南边|东边|西边|died|death|promise|identity|born",
    re.IGNORECASE,
)


def compact_receipt(receipt):
    channel, event, payload = receipt_event(receipt)
    # This is a receipt already projected for the owner. Preserve the semantic
    # rule/language fields (votes, agreements, transfers), not just speech.
    allowed = (
        LANGUAGE_FIELDS
        | STRUCTURE_FIELDS
        | {
            "speaker_name",
            "piece_id",
            "piece_name",
            "origin",
            "position",
            "last_visible_position",
            "object_id",
            "sender_id",
            "recipient_id",
            "result",
            "status",
            "members",
            "winner_ids",
            "loser_ids",
            "public_narrative",
            "description",
            "from",
            "to",
            "actual_to",
            "path",
            "path_visible_only",
            "participant_ids",
            "piece_type",
            "rest_remaining",
            "anchor",
            "name",
            "text",
        }
    )
    return {
        "receipt_id": receipt.get("receipt_id", receipt.get("id")),
        "turn": event.get("turn"),
        "channel": channel,
        "actor_id": event.get("actor_id"),
        "payload": {key: deepcopy(value) for key, value in payload.items() if key in allowed},
    }


def terms(text):
    text = text.casefold()
    words = set(re.findall(r"[a-z0-9_]{3,}", text))
    for run in re.findall(r"[\u4e00-\u9fff]+", text):
        words.update(run[i : i + 2] for i in range(len(run) - 1))
    return words - {"自己", "一个", "没有", "可以", "我们", "什么", "知道", "不要", "现在", "你们"}


def entry_context(entry):
    """Keep a bounded provenance sample in prompts; full sources stay durable."""
    value = {
        key: deepcopy(entry[key])
        for key in ("id", "owner_id", "text", "kind", "salience", "learned_turn")
        if key in entry
    }
    sources = entry.get("source_receipt_ids", [])
    value["source_receipt_ids"] = list(dict.fromkeys(sources[:1] + sources[-3:]))
    value["source_count"] = len(sources)
    return value


def recall(state, pid, extra=None, limit=6):
    own = state.get("private", {}).get(pid, {})
    receipts = state.get("receipts", {}).get(pid, [])
    latest = (extra or {}).get("latest_message") or {}
    speeches = [compact_receipt(r) for r in receipts if receipt_event(r)[0] not in NOISE]
    query = " ".join([own.get("goals", ""), latest.get("speech", "")])
    recent = [r["payload"].get("speech", "") for r in speeches[-3:]]
    query_terms = terms(query + " " + " ".join(recent))
    docs = []
    seen = set()
    for r in speeches:
        text = r["payload"].get("speech") or " ".join(
            str(r["payload"].get(k, "")) for k in ("public_narrative", "description", "result")
        )
        if not text or text in seen:
            continue
        seen.add(text)
        docs.append((r, text, terms(text)))
    frequencies = Counter(t for _, _, ts in docs for t in ts)
    ranked = []
    for index, (r, text, ts) in enumerate(docs):
        score = sum(math.log(1 + len(docs) / (1 + frequencies[t])) for t in ts & query_terms)
        if HIGH_SIGNAL.search(text):
            score += 2
        if score > 0:
            ranked.append((score, -index, r))
    ranked.sort(key=lambda item: (-item[0], -item[1]))
    entries = list(own.get("long_term_memory", {}).values())
    selected = sorted(
        entries,
        key=lambda e: (
            -len(terms(e.get("text", "")) & query_terms),
            e.get("salience") != "important",
            -e.get("learned_turn", 0),
        ),
    )[:8]
    return {
        "long_term": [entry_context(entry) for entry in selected],
        "archive_evidence": [deepcopy(r) for _, _, r in ranked[:limit]],
        "scope": "Only this owner's received archive. A source proves you heard/observed it, not that a speaker's claim is true. Do not treat old positions as current; previously received news is not newly learned.",
    }
