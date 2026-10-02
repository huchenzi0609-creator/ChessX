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
    "memory_compaction": 6144,
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
        retained = fitted.get("retained_important_facts")
        if stage == "memory_compaction" and isinstance(retained, list) and len(retained) > 1:
            retained.pop(0)
            continue
        receipts = fitted.get("receipts")
        if isinstance(receipts, list) and len(receipts) > 1:
            receipts.pop(0)
            continue
        recall = fitted.get("dialogue_recall", {})
        heard = recall.get("recent_heard") if isinstance(recall, dict) else None
        if isinstance(heard, list) and len(heard) > 1:
            heard.pop(0)
            continue
        own_utterances = recall.get("last_own_utterances") if isinstance(recall, dict) else None
        if isinstance(own_utterances, list) and len(own_utterances) > 1:
            own_utterances.pop(0)
            continue
        # Retrieved copies remain in the private archive/ledger. Window them by
        # relevance; never modify or acknowledge coverage of their sources.
        memory_recall = fitted.get("memory_recall", {})
        for field in ("archive_evidence", "long_term"):
            records = memory_recall.get(field) if isinstance(memory_recall, dict) else None
            if isinstance(records, list) and len(records) > 1:
                records.pop()
                break
        else:
            records = None
        if records is not None:
            continue
        navigation = fitted.get("navigation", {})
        options = navigation.get("movement_options") if isinstance(navigation, dict) else None
        if isinstance(options, list) and len(options) > 1:
            options.pop()
            continue
        approaches = (
            navigation.get("toward_known_people", []) if isinstance(navigation, dict) else []
        )
        reduced = False
        for approach in approaches:
            candidates = approach.get("options", [])
            if len(candidates) > 1:
                candidates.pop()
                reduced = True
                break
        if reduced:
            continue
        visited = navigation.get("visited_cells") if isinstance(navigation, dict) else None
        if isinstance(visited, list) and len(visited) > 1:
            visited.pop(0)
            continue
        journey = navigation.get("self_journey", {}) if isinstance(navigation, dict) else {}
        routes = journey.get("recent_actual_routes") if isinstance(journey, dict) else None
        if isinstance(routes, list) and len(routes) > 1:
            routes.pop(0)
            continue
        # Map evidence remains stored privately. Dates, bearings, positions and
        # sighting scope are essential; repeated journal IDs are reconstructible.
        map_records = navigation.get("known_locations", []) if isinstance(navigation, dict) else []
        route_records = routes if isinstance(routes, list) else []
        reduced = False
        for record in map_records + route_records:
            for key in ("receipt_id", "source_event_id"):
                if key in record:
                    record.pop(key)
                    reduced = True
        if reduced:
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
