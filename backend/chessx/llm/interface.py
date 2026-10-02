"""Model results are validated before they can enter the world event ledger."""

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True, slots=True)
class ModelResult:
    content: dict
    reasoning: str
    input_tokens: int
    output_tokens: int
    reasoning_tokens: int
    latency_ms: int


class ModelError(Exception):
    """A deliberately sanitized error safe to persist and show locally."""

    def __init__(self, code: str, message: str, *, details: dict | None = None):
        self.code = code
        self.message = message
        # Whitelist metadata; never carry upstream content, field values or paths.
        details = details or {}
        self.details = {}
        reasons = {
            "FORMAL_BUDGET_EXCEEDED",
            "CONTEXT_INPUT_BUDGET_EXCEEDED",
            "JSON_INVALID",
            "OBJECT_REQUIRED",
            "SCHEMA_MISMATCH",
            "REASONING_IN_FORMAL_OUTPUT",
            "RESPONSE_TRUNCATED",
            "ENVELOPE_INVALID",
            "CONTENT_MISSING",
            "REASONING_BUDGET_EXCEEDED",
            "INTERNAL_REFERENCE",
            "REPETITIVE_SPEECH",
        }
        if isinstance(details.get("reason"), str) and details["reason"] in reasons:
            self.details["reason"] = details["reason"]
        for key in (
            "actual_tokens",
            "limit",
            "violation_count",
            "reasoning_tokens",
            "formal_tokens",
            "requested_max_tokens",
        ):
            value = details.get(key)
            if type(value) is int and value >= 0:
                self.details[key] = value
        if isinstance(details.get("truncated_part"), str) and details["truncated_part"] in {
            "reasoning",
            "formal_output",
            "unknown",
        }:
            self.details["truncated_part"] = details["truncated_part"]
        keywords = details.get("schema_keywords", [])
        allowed = {
            "required",
            "additionalProperties",
            "enum",
            "const",
            "type",
            "anyOf",
            "oneOf",
            "minItems",
            "minLength",
            "uniqueItems",
            "maximum",
            "minimum",
            "pattern",
        }
        if "schema_keywords" in details and isinstance(keywords, list):
            self.details["schema_keywords"] = sorted(
                {k for k in keywords if isinstance(k, str) and k in allowed}
            )
        super().__init__(message)


class ModelAdapter(Protocol):
    async def generate(
        self, stage: str, context: dict, schema: dict, *, max_output_tokens: int = 512
    ) -> ModelResult: ...
