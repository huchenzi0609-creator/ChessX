"""Strict complete-response checks, with no guessed or truncated reasoning."""

import hashlib
import json
import os
import re
from pathlib import Path
from types import SimpleNamespace

import tiktoken
from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError

from .interface import ModelError


class TokenCounter:
    """An explicitly selected encoding, never inferred from the model name.

    The operator must select the encoding agreed with their provider. A named
    encoding makes counting deterministic; it does not certify provider billing.
    """

    def __init__(self, encoding: str, *, tokenizer_path: Path | None = None):
        os.environ.setdefault(
            "TIKTOKEN_CACHE_DIR", str(Path(__file__).resolve().parents[3] / ".cache" / "tiktoken")
        )
        self._tokenizer = None
        try:
            if encoding == "deepseek_v4":
                from tokenizers import Tokenizer

                path = (
                    tokenizer_path
                    or Path(__file__).resolve().parents[3]
                    / ".cache"
                    / "deepseek_v4"
                    / "tokenizer.json"
                )
                manifest = json.loads(path.with_name("manifest.json").read_text(encoding="utf-8"))
                if (
                    manifest.get("encoding") != "deepseek_v4"
                    or manifest.get("source_url")
                    != "https://cdn.deepseek.com/api-docs/deepseek_v4_tokenizer.zip"
                    or manifest.get("tokenizer_sha256")
                    != hashlib.sha256(path.read_bytes()).hexdigest()
                ):
                    raise ValueError("tokenizer provenance/hash mismatch")
                self._tokenizer = Tokenizer.from_file(str(path))
                self._tokenizer.no_truncation()
                self._tokenizer.no_padding()
                self.encoding = SimpleNamespace(name="deepseek_v4")
            else:
                self.encoding = tiktoken.get_encoding(encoding)
        except Exception:
            raise ModelError(
                "TOKENIZER_UNAVAILABLE", "Configured token encoding is unavailable."
            ) from None

    def count(self, text: str) -> int:
        if self._tokenizer is not None:
            return len(self._tokenizer.encode(text, add_special_tokens=False).ids)
        return len(self.encoding.encode(text, disallowed_special=()))


def _unique_pairs(pairs: list[tuple[str, object]]) -> dict:
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate key")
        result[key] = value
    return result


def extract_response(message: dict, *, probe: bool = False) -> tuple[str, str]:
    content = message.get("content")
    if not isinstance(content, str):
        raise ModelError(
            "MODEL_OUTPUT_INVALID",
            "Response must contain complete text content.",
            details={"reason": "CONTENT_MISSING"},
        )
    reasoning = message.get("reasoning_content", message.get("reasoning"))
    # A summary or token count is never an exposed reasoning trace.
    if reasoning is None:
        match = re.fullmatch(r"\s*<think>([\s\S]*?)</think>\s*([\s\S]+)", content)
        if match:
            reasoning, content = match.groups()
    if not isinstance(reasoning, str) or not reasoning.strip():
        code = "MODEL_COT_UNSUPPORTED" if probe else "MODEL_COT_MISSING"
        raise ModelError(code, "The model did not expose a nonempty reasoning trace.")
    if re.search(r"</?think(?:\s|>)", content, re.IGNORECASE):
        raise ModelError("MODEL_OUTPUT_INVALID", "Reasoning markers contaminated formal content.")
    return content, reasoning


def validate_output(
    content: str, reasoning: str, schema: dict, counter: TokenCounter, max_output_tokens: int
) -> dict:
    reasoning_tokens = counter.count(reasoning)
    if reasoning_tokens > 1000:
        raise ModelError(
            "COT_BUDGET_EXCEEDED",
            "Exposed reasoning exceeds the 1000-token limit.",
            details={
                "reason": "REASONING_BUDGET_EXCEEDED",
                "actual_tokens": reasoning_tokens,
                "limit": 1000,
            },
        )
    formal_tokens = counter.count(content)
    if formal_tokens > max_output_tokens:
        raise ModelError(
            "MODEL_OUTPUT_INVALID",
            "Formal output exceeds its configured token budget.",
            details={
                "reason": "FORMAL_BUDGET_EXCEEDED",
                "actual_tokens": formal_tokens,
                "limit": max_output_tokens,
            },
        )
    try:
        parsed = json.loads(
            content,
            object_pairs_hook=_unique_pairs,
            parse_constant=lambda _: (_ for _ in ()).throw(ValueError()),
        )
    except (ValueError, TypeError):
        raise ModelError(
            "MODEL_OUTPUT_INVALID",
            "Response must be a complete, unambiguous JSON object.",
            details={"reason": "JSON_INVALID"},
        ) from None
    if not isinstance(parsed, dict):
        raise ModelError(
            "MODEL_OUTPUT_INVALID",
            "Formal output must be a JSON object.",
            details={"reason": "OBJECT_REQUIRED"},
        )
    try:
        Draft202012Validator.check_schema(schema)
        errors = list(Draft202012Validator(schema).iter_errors(parsed))
    except SchemaError:
        raise ModelError(
            "MODEL_SCHEMA_INVALID", "The configured output schema is invalid."
        ) from None
    if errors:
        # jsonschema error messages can contain private input values.
        raise ModelError(
            "MODEL_OUTPUT_INVALID",
            "Formal output does not match the stage schema.",
            details={
                "reason": "SCHEMA_MISMATCH",
                "violation_count": len(errors),
                "schema_keywords": [e.validator for e in errors],
            },
        )

    def walk(value: object) -> None:
        if isinstance(value, dict):
            for key, item in value.items():
                if key.lower() in {"reasoning", "reasoning_content", "chain_of_thought", "cot"}:
                    raise ModelError(
                        "MODEL_OUTPUT_INVALID",
                        "Reasoning fields are not permitted in formal output.",
                        details={"reason": "REASONING_IN_FORMAL_OUTPUT"},
                    )
                if (
                    (key == "speech" or key == "text" and "event_refs" not in value)
                    and isinstance(item, str)
                    and counter.count(item) > 100
                ):
                    raise ModelError(
                        "SPEECH_BUDGET_EXCEEDED", "Formal speech exceeds the 100-token limit."
                    )
                walk(item)
        elif isinstance(value, list):
            for item in value:
                walk(item)

    walk(parsed)
    return parsed
