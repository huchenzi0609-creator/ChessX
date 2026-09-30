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

    def __init__(self, code: str, message: str):
        self.code = code
        self.message = message
        super().__init__(message)


class ModelAdapter(Protocol):
    async def generate(
        self, stage: str, context: dict, schema: dict, *, max_output_tokens: int = 512
    ) -> ModelResult: ...
