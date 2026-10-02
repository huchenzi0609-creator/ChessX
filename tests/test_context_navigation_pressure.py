"""Pressure from derived navigation must not displace private knowledge."""

from copy import deepcopy

import pytest
from chessx.contexts.budgets import fit_context, request_input_tokens
from chessx.llm import ModelError
from chessx.llm.validation import TokenCounter


@pytest.fixture
def counter():
    return TokenCounter("cl100k_base")


def context_with_single_navigation_examples():
    locations = [
        {
            "piece_id": f"person_{index}",
            "name": f"Person {index}",
            "position": {"row": index, "col": 7},
            "last_seen_turn": 10 + index,
            "current_visible": index == 7,
            "status_at_sighting": "alive",
            "position_scope": "current_view" if index == 7 else "last_seen",
            "source": "current_observation" if index == 7 else "owner_spatial_memory",
            "bearing": "东北",
            "distance_steps": 7,
            "delta_row": index - 7,
            "delta_col": 7,
            "distance_band": "较远",
            "age_turns": 20 - index,
            "receipt_id": f"owner:{index}",
            "source_event_id": f"world:{index}",
        }
        for index in range(8)
    ]
    source = {
        "receipt_id": "owner:news",
        "payload": {
            "speech": "The promised journey has not happened; the old report is unverified."
        },
    }
    return {
        "persona": "Owner-private design",
        "memory": "Earlier subjective memory",
        "goals": "Find the witness without inventing a current position.",
        "turn": 30,
        "observation": {
            "self": {"id": "owner", "position": {"row": 7, "col": 0}, "can_move": True},
            "pieces": [{"id": "person_7", "position": {"row": 7, "col": 7}}],
        },
        "receipts": [source],
        "dialogue_recall": {
            "recent_heard": [{"speaker_id": "person_0", "speech": source["payload"]["speech"]}],
            "latest_heard": {"speaker_id": "person_0", "speech": source["payload"]["speech"]},
            "last_own_utterances": ["I am considering that journey."],
        },
        "memory_recall": {
            "archive_evidence": [deepcopy(source)],
            "long_term": [
                {
                    "id": "old_news",
                    "owner_id": "owner",
                    "text": "A speaker previously reported a death; it remains an attributed claim.",
                    "kind": "heard_claim",
                    "learned_turn": 1,
                    "source_receipt_ids": ["owner:1"],
                }
            ],
        },
        "protocol": {"available_intents": ["wait", "move"], "latest_message": source},
        "navigation": {
            "current_position": {"row": 7, "col": 0},
            "coordinate_system": "North decreases row; east increases col.",
            "can_choose_move_in_this_stage": True,
            "geometric_move_targets": [[row, 0] for row in range(7)],
            "known_locations": locations,
            "movement_options": [{"target": [6, 0], "example": "derived " * 300}],
            "toward_known_people": [
                {
                    "piece_id": record["piece_id"],
                    "position_scope": record["position_scope"],
                    "options": [{"target": [6, 0], "example": "derived " * 300}],
                }
                for record in locations
            ],
            "visited_cells": [{"position": {"row": 7, "col": 0}, "example": "archived " * 300}],
            "self_journey": {
                "current_position": {"row": 7, "col": 0},
                "previous_position": {"row": 6, "col": 0},
                "arrival_from": "北",
                "recent_actual_routes": [
                    {"from": [6, 0], "to": [7, 0], "example": "archived " * 300}
                ],
            },
            "pending_move": {
                "planned_target": [6, 0],
                "committed_origin": [7, 0],
                "intent_status": "not_committed_not_visited",
            },
        },
    }


def reduced_navigation_floor(original):
    """Expected knowledge retained when every advisory example is omitted."""
    expected = deepcopy(original)
    navigation = expected["navigation"]
    for key in ("movement_options", "toward_known_people", "visited_cells"):
        navigation[key] = []
    navigation["self_journey"]["recent_actual_routes"] = []
    for location in navigation["known_locations"]:
        for key in (
            "delta_row",
            "delta_col",
            "distance_band",
            "age_turns",
            "receipt_id",
            "source_event_id",
        ):
            location.pop(key)
    navigation["windowed"] = True
    navigation["windowed_scope"] = (
        "Derived examples/history only; all known dated positions retained."
    )
    return expected


@pytest.mark.parametrize("stage", ["action", "ordinary", "negotiation", "memory", "settlement"])
def test_single_advisory_records_can_be_omitted_without_forgetting_sources(counter, stage):
    original = context_with_single_navigation_examples()
    snapshot = deepcopy(original)
    expected = reduced_navigation_floor(original)
    schema = {"type": "object"}
    limit = request_input_tokens(stage, expected, schema, counter, 512)

    fitted = fit_context(stage, original, schema, counter, max_output_tokens=512, input_limit=limit)

    assert fitted == expected
    assert original == snapshot
    assert request_input_tokens(stage, fitted, schema, counter, 512) <= limit
    assert len(fitted["navigation"]["known_locations"]) == 8
    assert fitted["navigation"]["pending_move"] == original["navigation"]["pending_move"]
    for field in ("observation", "receipts", "memory_recall", "dialogue_recall", "protocol"):
        assert fitted[field] == original[field]


def test_small_navigation_is_not_projected_without_pressure(counter):
    original = reduced_navigation_floor(context_with_single_navigation_examples())
    original["navigation"].pop("windowed")
    original["navigation"].pop("windowed_scope")
    count = request_input_tokens("action", original, {}, counter, 512)
    fitted = fit_context("action", original, {}, counter, max_output_tokens=512, input_limit=count)
    assert fitted == original and fitted is not original


def test_compact_dated_map_suffices_without_cutting_advisory_route(counter):
    original = reduced_navigation_floor(context_with_single_navigation_examples())
    original["navigation"]["known_locations"] = context_with_single_navigation_examples()[
        "navigation"
    ]["known_locations"]
    original["navigation"]["movement_options"] = [{"target": [6, 0], "unknown_path_count": 1}]
    expected = reduced_navigation_floor(original)
    expected["navigation"]["movement_options"] = deepcopy(
        original["navigation"]["movement_options"]
    )
    limit = request_input_tokens("action", expected, {}, counter, 512)
    fitted = fit_context("action", original, {}, counter, max_output_tokens=512, input_limit=limit)
    assert fitted == expected
    for location in fitted["navigation"]["known_locations"]:
        assert location["last_seen_turn"] <= original["turn"]
        assert location["current_visible"] == (location["position_scope"] == "current_view")
        assert "source" in location and "position" in location


@pytest.mark.parametrize("field", ["persona", "goals", "memory"])
def test_navigation_pressure_does_not_hide_oversized_character_anchors(counter, field):
    original = context_with_single_navigation_examples()
    original[field] = "essential anchor " * 5000
    snapshot = deepcopy(original)
    with pytest.raises(ModelError) as error:
        fit_context("action", original, {}, counter, max_output_tokens=512, input_limit=8192)
    assert error.value.code == "CONTEXT_BUDGET_EXCEEDED"
    assert original == snapshot


def test_navigation_cannot_pay_for_a_mandatory_context_shortfall(counter):
    original = reduced_navigation_floor(context_with_single_navigation_examples())
    count = request_input_tokens("action", original, {}, counter, 512)
    with pytest.raises(ModelError) as error:
        fit_context("action", original, {}, counter, max_output_tokens=512, input_limit=count - 1)
    assert error.value.code == "CONTEXT_BUDGET_EXCEEDED"
