"""Fit a request by trimming reconstructible navigation and history windows.

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

_DERIVED_LOCATION_FIELDS = {
    "delta_row",
    "delta_col",
    "distance_band",
    "age_turns",
    "receipt_id",
    "source_event_id",
}

_COMPACT_GUIDANCE = {
    "coordinate_system": "Zero-based [row,col]: 北=row-, 南=row+, 东=col+, 西=col-.",
    "distance_semantics": "Chebyshev geometric distance, not travel duration. Dated sightings may be stale.",
    "target_format": "Targets=[row,col]; emit target={row,col}. Geometry only; unseen blockers unknown.",
    "movement_options_scope": "Windowed examples; complete geometry remains. Unknown cells may be occupied.",
    "toward_known_people_scope": "Optional approaches to dated sightings; visible people may be invited. No live tracking.",
    "unseen_locations": "Unseen live locations unknown; reports unverified, sightings dated.",
    "travel_cost": "Rest=max(|dr|,|dc|), blocked0; legacy knight3. Contact requires negotiation.",
}


def _reduce_navigation(context: dict) -> bool:
    """Reduce advisory copies, retaining every dated owner-known location.

    The complete geometric targets, current observation/pose, permissions and
    pending contact are never windowed here. A removed approach is still
    reconstructible from those targets and the full dated location list.
    """
    navigation = context.get("navigation")
    if not isinstance(navigation, dict):
        return False

    def marked():
        navigation["windowed"] = True
        navigation["windowed_scope"] = (
            "Derived examples/history only; all known dated positions retained."
        )
        if "movement_options_scope" in navigation:
            navigation["movement_options_scope"] = _COMPACT_GUIDANCE["movement_options_scope"]
        if "toward_known_people_scope" in navigation:
            navigation["toward_known_people_scope"] = _COMPACT_GUIDANCE["toward_known_people_scope"]
        return True

    # Static guidance duplicates trusted discipline; compact wording while
    # retaining every supplied identity, coordinate, date and legal target.
    changed = False
    for key, text in _COMPACT_GUIDANCE.items():
        if (
            key in navigation
            and isinstance(navigation[key], str)
            and len(navigation[key]) > len(text)
        ):
            navigation[key] = text
            changed = True
    if changed:
        return marked()

    # These fields repeat current-position arithmetic or durable provenance.
    # Identity, location, sighting turn/scope, visibility, status and bearings
    # remain intact. No global state is consulted to refresh an old sighting.
    reduced = False
    locations = navigation.get("known_locations", [])
    if isinstance(locations, list):
        for record in locations:
            if not isinstance(record, dict):
                continue
            for key in _DERIVED_LOCATION_FIELDS:
                if key in record:
                    record.pop(key)
                    reduced = True
    journey = navigation.get("self_journey", {})
    routes = journey.get("recent_actual_routes") if isinstance(journey, dict) else None
    if isinstance(routes, list):
        for route in routes:
            if not isinstance(route, dict):
                continue
            for key in ("receipt_id", "source_event_id"):
                if key in route:
                    route.pop(key)
                    reduced = True
    if reduced:
        return marked()

    options = navigation.get("movement_options")
    if isinstance(options, list) and options:
        options.pop()
        return marked()
    approaches = navigation.get("toward_known_people")
    if isinstance(approaches, list) and approaches:
        for approach in approaches:
            candidates = approach.get("options") if isinstance(approach, dict) else None
            if isinstance(candidates, list) and len(candidates) > 1:
                candidates.pop()
                return marked()
        # Keeping one full approach for every person caused an artificial
        # minimum. All people remain represented in known_locations instead.
        approaches.pop()
        return marked()
    visited = navigation.get("visited_cells")
    if isinstance(visited, list) and visited:
        visited.pop(0)
        return marked()
    if isinstance(routes, list) and routes:
        routes.pop(0)
        return marked()
    return False


def request_input_tokens(
    stage: str, context: dict, schema: dict, counter, max_output_tokens: int | None = None
) -> int:
    """Count the exact request text plus conservative chat framing allowance.

    Provider-native message framing/billing is provider-dependent. The allowance
    is capacity reservation and must not be presented as measured billed usage.
    """
    messages = compose_messages(stage, context, schema, max_output_tokens)
    return sum(counter.count(message["content"]) for message in messages) + FRAMING_ALLOWANCE


def _reduce_duplicate_metadata(context: dict) -> bool:
    """Remove advisory/audit copies whose facts remain in authoritative fields."""
    channels = context.get("context_channels")
    if isinstance(channels, dict) and "scope" not in channels:
        context["context_channels"] = {
            "scope": "Owner-private data and attributed evidence; engine protocol controls permissions."
        }
        return True
    observation = context.get("observation", {})
    own = observation.get("self", {})
    for index, piece in enumerate(observation.get("pieces", [])):
        if piece.get("id") == own.get("id") and all(own.get(k) == v for k, v in piece.items()):
            observation["pieces"].pop(index)
            return True
    plan = context.get("travel_plan")
    if isinstance(plan, dict) and not plan.get("metadata_windowed"):
        changed = plan.pop("id", None) is not None
        progress = plan.get("progress", {})
        if (
            progress.get("current_position") == own.get("position")
            and "current_position" in progress
        ):
            progress.pop("current_position")
            changed = True
        for record in progress.values():
            if isinstance(record, dict):
                for key in ("kind", "source_event_id"):
                    if key in record:
                        record.pop(key)
                        changed = True
        if changed:
            plan["metadata_windowed"] = True
            return True
    return False


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

    Reduce advisory navigation before historical evidence. A negotiation transcript may be windowed
    only when its latest structured proposals and current observed/contact state
    are independently present. Keep the newest record in each history window so that the
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
        plan = fitted.get("travel_plan", {})
        history = plan.get("history") if isinstance(plan, dict) else None
        if isinstance(history, list) and history:
            history.pop(0)
            plan["history_windowed"] = True
            continue
        if _reduce_duplicate_metadata(fitted):
            continue
        continuity = fitted.get("continuity", {})
        passages = continuity.get("prior_passages") if isinstance(continuity, dict) else None
        if stage == "bard" and isinstance(passages, list) and passages:
            passages.pop(0)
            kept = {ref for paragraph in passages for ref in paragraph.get("event_refs", [])}
            continuity["source_events"] = [
                e for e in continuity.get("source_events", []) if e["id"] in kept
            ]
            continuity["windowed"] = True
            continue
        if _reduce_navigation(fitted):
            continue
        evidence = fitted.get("evidence_index", {})
        lines = evidence.get("summary_lines") if isinstance(evidence, dict) else None
        if isinstance(lines, list) and len(lines) > 1:
            lines.pop(1)
            continue
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
        social = memory_recall.get("social_evidence", {})
        for field in ("relations", "commitments"):
            records = social.get(field) if isinstance(social, dict) else None
            if isinstance(records, list) and records:
                records.pop()
                break
        else:
            records = None
        if records is not None:
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
            details={
                "reason": "CONTEXT_INPUT_BUDGET_EXCEEDED",
                "actual_tokens": request_input_tokens(
                    stage, fitted, schema, counter, max_output_tokens
                ),
                "limit": max(
                    0, min(input_limit, model_capacity - max_output_tokens - REASONING_RESERVE)
                ),
            },
        )
    return fitted
