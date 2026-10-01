"""Fit a request by trimming only reconstructible recent-history windows.

All character anchors, present goals/memory and narrator participant memories
are immutable inputs. Oversized essential inputs pause before network activity.
"""

from copy import deepcopy

from chessx.llm import ModelError
from chessx.llm.provider import compose_messages

REASONING_RESERVE = 1000
FRAMING_ALLOWANCE = 128
STAGE_INPUT_LIMITS = {
    "memory": 6144,
    "memory_editor": 6144,
    "battle_narrator": 12288,
    "outcome": 12288,
    "bard": 12288,
}


def request_input_tokens(
    stage: str, context: dict, schema: dict, counter, max_output_tokens: int | None = None
) -> int:
    """Count the exact request text plus conservative chat framing allowance.

    Provider-native message framing/billing is provider-dependent. The allowance
    is capacity reservation and must not be presented as measured billed usage.
    """
    messages = compose_messages(stage, context, schema, max_output_tokens)
    return sum(counter.count(message["content"]) for message in messages) + FRAMING_ALLOWANCE


def fit_context(
    stage: str,
    context: dict,
    schema: dict,
    counter,
    *,
    max_output_tokens: int,
    model_capacity: int = 16384,
    input_limit: int | None = None,
) -> dict:
    """Return an independent fitted DTO, preserving the canonical receipt archive.

    Drop oldest receipt records first. A negotiation transcript may be windowed
    only when its latest structured proposals and current observed/contact state
    are independently present. Keep the newest record in each window so that the
    immediate predecessor's delivery remains available; otherwise pause.
    """
    if max_output_tokens < 1 or model_capacity < 1:
        raise ModelError(
            "CONTEXT_BUDGET_EXCEEDED", "Model capacity and output reservation must be positive."
        )
    fitted = deepcopy(context)
    if input_limit is None:
        input_limit = STAGE_INPUT_LIMITS.get(stage, 4096)
    if input_limit < 1:
        raise ModelError("CONTEXT_BUDGET_EXCEEDED", "Stage input limit must be positive.")

    def fits() -> bool:
        count = request_input_tokens(stage, fitted, schema, counter, max_output_tokens)
        return (
            count <= input_limit and count + max_output_tokens + REASONING_RESERVE <= model_capacity
        )

    while not fits():
        receipts = fitted.get("receipts")
        if isinstance(receipts, list) and len(receipts) > 1:
            receipts.pop(0)
            continue
        protocol = fitted.get("protocol", {})
        messages = protocol.get("messages") if isinstance(protocol, dict) else None
        current_state_present = bool(fitted.get("observation")) or (
            isinstance(protocol, dict) and "position" in protocol
        )
        if (
            isinstance(messages, list)
            and len(messages) > 1
            and "latest_proposals" in protocol
            and current_state_present
        ):
            messages.pop(0)
            continue
        raise ModelError(
            "CONTEXT_BUDGET_EXCEEDED",
            "Essential stage input exceeds its token budget or configured model capacity.",
        )
    return fitted
