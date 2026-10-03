"""Spatial recall stays owner-scoped, historical and faithful to actual routes."""

import json
from copy import deepcopy

import pytest
from chessx.contexts.agency import navigation, pending_move_context
from chessx.contexts.spatial import relative_position, spatial_recall


def observation(kind="rook", position=None):
    position = position or {"row": 3, "col": 3}
    radius = 1
    return {
        "self": {
            "id": "a",
            "name": "Alice",
            "type": kind,
            "position": position,
            "can_move": True,
            "rest_at_start": 0,
        },
        "board": {"rows": 8, "cols": 8},
        "visible_cells": [
            {"row": row, "col": col}
            for row in range(max(0, position["row"] - radius), min(8, position["row"] + radius + 1))
            for col in range(max(0, position["col"] - radius), min(8, position["col"] + radius + 1))
        ],
        "pieces": [],
    }


def receipt(channel, payload, actor="a", turn=1, seq=1):
    return {
        "receipt_id": f"a:{seq}",
        "source_event_id": f"world:{seq}",
        "channel": channel,
        "payload": {"actor_id": actor, "turn": turn, "payload": payload},
    }


@pytest.mark.parametrize(
    ("dr", "dc", "bearing"),
    [
        (-1, 0, "北"),
        (1, 0, "南"),
        (0, 1, "东"),
        (0, -1, "西"),
        (-1, 1, "东北"),
        (-1, -1, "西北"),
        (1, 1, "东南"),
        (1, -1, "西南"),
        (0, 0, "同格"),
    ],
)
def test_compass_and_chebyshev_distance(dr, dc, bearing):
    relative = relative_position({"row": 3, "col": 3}, {"row": 3 + dr, "col": 3 + dc})
    assert relative["bearing"] == bearing
    assert relative["distance_steps"] == max(abs(dr), abs(dc))
    assert (relative["delta_row"], relative["delta_col"]) == (dr, dc)


def test_knight_actual_journey_has_only_two_endpoints_and_no_fake_visited_cells():
    receipts = [
        receipt(
            "MoveCommitted",
            {
                "piece_id": "a",
                "origin": {"row": 3, "col": 3},
                "position": {"row": 1, "col": 4},
                "path": [{"row": 3, "col": 3}, {"row": 1, "col": 4}],
            },
        ),
        receipt("MoveProposed", {"piece_id": "a", "target": {"row": 0, "col": 6}}, seq=2),
    ]
    before = deepcopy(receipts)
    result = navigation(
        observation("knight", {"row": 1, "col": 4}), receipts, "a", "action", turn=2
    )
    journey = result["self_journey"]
    assert journey["recent_actual_routes"][0]["path"] == [[3, 3], [1, 4]]
    assert journey["recent_actual_routes"][0]["is_jump"]
    assert journey["previous_position"] == {"row": 3, "col": 3}
    assert journey["arrival_from"] == "西南"
    assert {tuple(item["position"].values()) for item in result["visited_cells"]} == {
        (3, 3),
        (1, 4),
    }
    assert receipts == before
    assert all(option["rest_turns_if_success"] == 2 for option in result["movement_options"])


def test_last_visible_path_sighting_keeps_age_provenance_and_does_not_claim_destination():
    receipts = [
        receipt(
            "MoveCommitted",
            {
                "piece_id": "b",
                "piece_name": "Bob",
                "path": [{"row": 2, "col": 2}],
                "path_visible_only": True,
                "last_visible_position": {"row": 2, "col": 2},
            },
            actor="b",
            turn=3,
        )
    ]
    saved = {
        "seen_people": {
            "b": {
                "name": "Bob",
                "position": {"row": 4, "col": 4},
                "last_seen_turn": 2,
                "source_event_id": "world:0",
                "private": "NO_LEAK",
            }
        },
        "reported_locations": [{"piece_id": "c", "position": {"row": 1, "col": 1}}],
    }
    result = navigation(observation(), receipts, "a", "action", turn=50, spatial_memory=saved)
    entry = result["known_locations"][0]
    assert entry["position"] == {"row": 2, "col": 2}
    assert entry["position_scope"] == "last_seen_on_partial_path"
    assert entry["age_turns"] == 47 and entry["bearing"] == "西北"
    assert entry["source_event_id"] == "world:1" and not entry["current_visible"]
    assert "NO_LEAK" not in json.dumps(result)
    assert all(item["piece_id"] != "c" for item in result["known_locations"])


def test_stationary_old_sighting_is_recalled_and_current_view_can_update_it():
    saved = {
        "seen_people": {
            "b": {
                "name": "Bob",
                "position": {"row": 6, "col": 6},
                "last_seen_turn": 10,
                "source_event_id": "world:10",
            }
        }
    }
    obs = observation()
    stale = spatial_recall(obs, [], "a", turn=50, spatial_memory=saved)["known_locations"][0]
    assert stale["age_turns"] == 40 and stale["position_scope"] == "last_seen"
    assert stale["bearing"] == "东南" and stale["distance_steps"] == 3
    assert not stale["current_visible"]
    obs["pieces"] = [{"id": "b", "name": "Bob", "position": {"row": 3, "col": 4}}]
    current = spatial_recall(obs, [], "a", turn=50, spatial_memory=saved)["known_locations"][0]
    assert current["position"] == {"row": 3, "col": 4}
    assert current["age_turns"] == 0 and current["current_visible"]
    assert current["source"] == "current_observation"


def test_contact_records_blocker_cell_but_does_not_teleport_mover_or_ordinary_members():
    contacts = [
        receipt(
            "ConversationCreated",
            {
                "kind": "negotiation",
                "members": ["mover", "b", "a"],
                "position": {"row": 6, "col": 6},
                "participants": [{"id": "mover", "name": "Mover"}, {"id": "b", "name": "Bob"}],
            },
            actor="mover",
            turn=8,
        ),
        receipt("ConversationCreated", {"kind": "ordinary", "members": ["c", "a"]}, seq=2),
    ]
    result = spatial_recall(observation(), contacts, "a", turn=50)
    assert [item["piece_id"] for item in result["known_locations"]] == ["b"]
    assert result["known_locations"][0]["name"] == "Bob"
    assert result["known_locations"][0]["position"] == {"row": 6, "col": 6}
    assert result["visited_total_count"] == 1


def test_route_risk_distinguishes_visible_occupants_empty_cells_and_unknown_cells():
    obs = observation()
    obs["pieces"] = [{"id": "b", "name": "Bob", "position": {"row": 3, "col": 4}}]
    result = navigation(obs, [], "a", "action", turn=1)
    options = {tuple(option["target"]): option for option in result["movement_options"]}
    endpoint = options[(3, 4)]
    assert endpoint["destination_knowledge"] == "visible_occupied"
    assert endpoint["known_contacts"] == [
        {"position": [3, 4], "occupants": ["b"], "at_destination": True}
    ]
    farther = options[(3, 5)]
    assert farther["destination_knowledge"] == "unknown"
    assert farther["unknown_path_count"] == 1
    assert farther["known_contacts"][0]["at_destination"] is False
    assert farther["rest_turns_if_success"] == 2
    assert options[(3, 2)]["destination_knowledge"] == "visible_empty"
    assert len(result["movement_options"]) <= 12
    assert len(result["geometric_move_targets"]) == 14


def test_route_and_visited_windows_are_bounded_without_truncating_owner_archive():
    receipts = []
    for seq in range(1, 40):
        row = (seq - 1) // 7
        col = (seq - 1) % 7
        receipts.append(
            receipt(
                "MoveCommitted",
                {
                    "piece_id": "a",
                    "origin": {"row": row, "col": col},
                    "position": {"row": row, "col": col + 1},
                },
                turn=seq,
                seq=seq,
            )
        )
    original = deepcopy(receipts)
    result = spatial_recall(observation(position={"row": 5, "col": 4}), receipts, "a", turn=40)
    assert len(result["self_journey"]["recent_actual_routes"]) == 4
    assert len(result["visited_cells"]) == 24
    assert result["visited_total_count"] > 24
    assert receipts == original


def test_spoken_directions_are_not_promoted_to_verified_map_coordinates():
    heard = [
        receipt(
            "ConversationMessage",
            {"speaker_id": "b", "speaker_name": "Bob", "speech": "老牧人在东北三格处。"},
            actor="b",
        )
    ]
    result = spatial_recall(observation(), heard, "a", turn=50)
    assert result["known_locations"] == []


@pytest.mark.parametrize("kind", ["rook", "bishop", "king", "knight"])
def test_approach_candidates_follow_geometry_and_make_progress_toward_old_sighting(kind):
    goal = {"row": 0, "col": 7}
    remembered = {
        "seen_people": {
            "b": {
                "name": "Bob",
                "position": goal,
                "last_seen_turn": 2,
                "source_event_id": "world:2",
            }
        }
    }
    result = navigation(observation(kind), [], "a", "action", turn=50, spatial_memory=remembered)
    approach = result["toward_known_people"][0]
    assert approach["piece_id"] == "b" and approach["reference_position"] == [0, 7]
    assert approach["position_scope"] == "last_seen" and not approach["already_visible"]
    assert approach["distance_before"] == 4
    assert 1 <= len(approach["options"]) <= 2
    legal_targets = {tuple(target) for target in result["geometric_move_targets"]}
    for candidate in approach["options"]:
        assert tuple(candidate["target"]) in legal_targets
        assert candidate["distance_after"] < approach["distance_before"]
        assert (
            candidate["progress_steps"] == approach["distance_before"] - candidate["distance_after"]
        )
        assert candidate["rest_turns_if_success"] == (
            2
            if kind == "knight"
            else max(abs(candidate["target"][0] - 3), abs(candidate["target"][1] - 3))
        )
    if kind == "bishop":
        # This target has opposite square parity. A useful next step is still
        # possible, but the context must not invent a direct bishop destination.
        assert all(candidate["target"] != [0, 7] for candidate in approach["options"])


def test_approach_candidates_prefer_free_progress_and_still_expose_contact_cost():
    obs = observation("queen")
    obs["pieces"] = [{"id": "blocker", "name": "Blocker", "position": {"row": 3, "col": 4}}]
    remembered = {
        "seen_people": {
            "goal": {
                "name": "Goal",
                "position": {"row": 3, "col": 7},
                "last_seen_turn": 2,
                "source_event_id": "world:2",
            }
        }
    }
    result = navigation(obs, [], "a", "action", turn=50, spatial_memory=remembered)
    approach = next(entry for entry in result["toward_known_people"] if entry["piece_id"] == "goal")
    assert approach["options"] and all(
        not candidate["known_contact_ids"] for candidate in approach["options"]
    )
    blocker = next(
        entry for entry in result["toward_known_people"] if entry["piece_id"] == "blocker"
    )
    assert blocker["already_visible"]
    assert blocker["options"][0]["known_contact_ids"] == ["blocker"]


def test_unknown_people_never_get_approach_candidates_and_rest_never_produces_move_options():
    unknown = navigation(observation(), [], "a", "action", turn=50)
    assert unknown["toward_known_people"] == []
    obs = observation()
    obs["self"]["can_move"] = False
    obs["self"]["rest_at_start"] = 1
    remembered = {
        "seen_people": {
            "b": {
                "name": "Bob",
                "position": {"row": 3, "col": 7},
                "last_seen_turn": 2,
                "source_event_id": "world:2",
            }
        }
    }
    result = navigation(obs, [], "a", "action", turn=50, spatial_memory=remembered)
    assert result["toward_known_people"][0]["options"] == []
    assert result["geometric_move_targets"] == []


@pytest.mark.parametrize("kind", ["rook", "pawn"])
def test_orthogonal_piece_can_approach_diagonal_place_without_chebyshev_reduction(kind):
    remembered = {
        "seen_people": {
            "b": {
                "name": "Bob",
                "position": {"row": 0, "col": 0},
                "last_seen_turn": 2,
                "source_event_id": "world:2",
            }
        }
    }
    result = navigation(observation(kind), [], "a", "action", turn=50, spatial_memory=remembered)
    approach = result["toward_known_people"][0]
    assert approach["distance_before"] == 3
    assert len(approach["options"]) == 2
    legal = {tuple(target) for target in result["geometric_move_targets"]}
    for candidate in approach["options"]:
        assert tuple(candidate["target"]) in legal
        assert candidate["distance_after"] == 3 and candidate["progress_steps"] == 0
        assert candidate["axis_progress_steps"] > 0
        assert candidate["target"][0] <= 3 and candidate["target"][1] <= 3
    if kind == "pawn":
        assert {tuple(candidate["target"]) for candidate in approach["options"]} == {(2, 3), (3, 2)}


@pytest.mark.parametrize("kind", ["rook", "pawn", "king", "bishop", "knight"])
def test_approach_options_never_increase_either_distance_or_emit_zero_progress(kind):
    origin = {"row": 3, "col": 3}
    goal = {"row": 0, "col": 7}
    remembered = {
        "seen_people": {
            "b": {
                "name": "Bob",
                "position": goal,
                "last_seen_turn": 2,
                "source_event_id": "world:2",
            }
        }
    }
    result = navigation(observation(kind), [], "a", "action", turn=50, spatial_memory=remembered)
    approach = result["toward_known_people"][0]
    axis_before = abs(origin["row"] - goal["row"]) + abs(origin["col"] - goal["col"])
    for candidate in approach["options"]:
        row, col = candidate["target"]
        axis_after = abs(row - goal["row"]) + abs(col - goal["col"])
        assert candidate["distance_after"] <= approach["distance_before"]
        assert axis_after <= axis_before
        assert candidate["axis_progress_steps"] == axis_before - axis_after
        assert candidate["progress_steps"] > 0 or candidate["axis_progress_steps"] > 0


def test_pending_mover_knows_passage_destination_without_creating_a_visited_pose():
    obs = observation()
    work = {
        "actor_id": "a",
        "intent": {"kind": "move", "target": {"row": 3, "col": 7}},
        "origin": {"row": 3, "col": 3},
    }
    protocol = {"position": {"row": 3, "col": 4}, "mode": "equal"}
    before = deepcopy((obs, work, protocol))
    pending = pending_move_context(obs, "a", "negotiation", protocol, work)
    assert pending["pending_move"]["planned_target"] == [3, 7]
    assert pending["pending_move"]["committed_origin"] == [3, 3]
    assert pending["pending_move"]["bearing"] == "东"
    assert pending["pending_move"]["contact_kind"] == "passage"
    assert pending["pending_move"]["intent_status"] == "not_committed_not_visited"
    nav = navigation(obs, [], "a", "negotiation", turn=1)
    nav.update(pending)
    assert nav["self_journey"]["current_position"] == {"row": 3, "col": 3}
    assert [cell["position"] for cell in nav["visited_cells"]] == [{"row": 3, "col": 3}]
    assert (obs, work, protocol) == before
    protocol["position"] = {"row": 3, "col": 7}
    assert (
        pending_move_context(obs, "a", "negotiation", protocol, work)["pending_move"][
            "contact_kind"
        ]
        == "co-location"
    )


def test_blocker_receives_only_admitted_contact_not_other_mover_hidden_intent():
    work = {
        "actor_id": "hidden_mover",
        "intent": {"kind": "move", "target": {"row": 7, "col": 7}},
        "origin": {"row": 0, "col": 0},
        "path": ["PRIVATE_ROUTE"],
    }
    protocol = {"position": {"row": 3, "col": 3}, "mode": "b_advantage"}
    result = pending_move_context(observation(), "a", "negotiation", protocol, work)
    assert list(result) == ["interaction_contact"]
    assert result["interaction_contact"]["position"] == [3, 3]
    encoded = json.dumps(result)
    assert (
        "PRIVATE_ROUTE" not in encoded
        and "planned_target" not in encoded
        and "committed_origin" not in encoded
    )


@pytest.mark.parametrize("stage", ["ordinary", "action", "settlement"])
def test_pending_move_is_not_exposed_outside_negotiation(stage):
    work = {"actor_id": "a", "intent": {"kind": "move", "target": {"row": 3, "col": 7}}}
    assert (
        pending_move_context(observation(), "a", stage, {"position": {"row": 3, "col": 4}}, work)
        == {}
    )


def test_invalid_target_never_becomes_pending_location():
    work = {"actor_id": "a", "intent": {"kind": "move", "target": {"row": 99, "col": 7}}}
    result = pending_move_context(observation(), "a", "negotiation", {}, work)
    assert result == {}
