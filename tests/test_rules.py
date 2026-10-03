from copy import deepcopy

import pytest
from chessx.domain.errors import RuleError
from chessx.domain.models import Scenario
from chessx.domain.scenarios import create_world
from chessx.rules.actions import (
    apply_primary,
    authorize_join,
    create_transfer_offer,
    kill_loser,
    merge_battles,
    release_loser,
    resolve_transfer,
    validate_battle_partition,
)
from chessx.rules.geometry import (
    begin_turn,
    blocker_groups,
    can_threaten,
    commit_move,
    legal_move,
    movement_rest,
    negotiation_mode,
    occupants,
    trace_path,
    visible_positions,
)
from chessx.rules.invariants import assert_invariants


def pos(row, col):
    return {"row": row, "col": col}


def world(types=("rook", "pawn", "knight", "bishop"), positions=None):
    positions = positions or [pos(3, 1), pos(3, 3), pos(3, 4), pos(4, 4)]
    scenario = Scenario.model_validate(
        {
            "scenario_id": "rules-test",
            "title": "Rule fixture",
            "board": {"rows": 8, "cols": 8},
            "pieces": [
                {
                    "id": str(i),
                    "name": f"角色{i}",
                    "type": kind,
                    "color": "白",
                    "position": positions[i],
                }
                for i, kind in enumerate(types)
            ],
            "objects": [
                {
                    "id": "seal",
                    "name": "印信",
                    "location": {"kind": "ground", "position": positions[0]},
                }
            ],
        }
    )
    return create_world(scenario, {}, "w")


def battle(state, bid, members, status="active", anchor=None):
    state["battles"][bid] = {
        "id": bid,
        "status": status,
        "members": list(members),
        "anchor": anchor or pos(3, 3),
        "round": 1,
        "max_rounds": 4,
        "created_turn": 0,
    }
    for pid in members:
        state["pieces"][pid]["battle_id"] = bid
    return state["battles"][bid]


def settling_world():
    state = world()
    b = battle(state, "b", ["0", "1", "2"], "settling")
    b.update(winner_ids=["0", "2"], loser_ids=["1"])
    return state


def test_pawn_four_orthogonal_steps_and_four_diagonal_threats():
    state = world(types=("pawn",), positions=[pos(3, 3)])
    pawn = state["pieces"]["0"]
    for target in [pos(2, 3), pos(4, 3), pos(3, 2), pos(3, 4)]:
        legal_move(state, "0", target)
        assert not can_threaten(pawn, target)
    for target in [pos(2, 2), pos(2, 4), pos(4, 2), pos(4, 4)]:
        assert can_threaten(pawn, target)
        with pytest.raises(RuleError) as error:
            legal_move(state, "0", target)
        assert error.value.code == "MOVE_GEOMETRY_INVALID"
    state["pieces"]["0"]["color"] = "任意颜色"
    assert can_threaten(pawn, pos(2, 2))


def test_pawn_diagonal_requires_other_live_piece_not_object_or_tombstone():
    state = world(types=("pawn", "rook"), positions=[pos(3, 3), pos(4, 4)])
    legal_move(state, "0", pos(4, 4))
    state["pieces"]["1"]["alive"] = False
    state["objects"]["seal"]["location"]["position"] = pos(4, 4)
    state["tombstones"]["t"] = {
        "id": "t",
        "piece_id": "1",
        "position": pos(4, 4),
        "death_event_id": "e",
    }
    with pytest.raises(RuleError):
        legal_move(state, "0", pos(4, 4))
    assert occupants(state, pos(4, 4)) == []


def test_knight_has_no_intermediate_path_and_origin_is_not_scanned():
    state = world(types=("knight", "rook", "pawn"), positions=[pos(1, 1), pos(2, 1), pos(1, 1)])
    legal_move(state, "0", pos(3, 2))
    assert trace_path(state["pieces"]["0"], pos(3, 2)) == [pos(1, 1), pos(3, 2)]
    assert blocker_groups(state, "0", pos(3, 2)) == []
    assert movement_rest(state["pieces"]["0"], pos(1, 1), pos(1, 1)) == 0
    assert movement_rest(state["pieces"]["0"], pos(1, 1), pos(3, 2)) == 2


@pytest.mark.parametrize(
    "piece_type,target",
    [
        ("king", pos(5, 5)),
        ("rook", pos(5, 5)),
        ("bishop", pos(3, 5)),
        ("knight", pos(4, 4)),
        ("pawn", pos(3, 5)),
    ],
)
def test_illegal_geometry(piece_type, target):
    state = world(types=(piece_type,), positions=[pos(3, 3)])
    with pytest.raises(RuleError):
        legal_move(state, "0", target)


def test_path_blocker_groups_include_all_live_occupants_even_resting():
    state = world(
        types=("rook", "pawn", "knight", "bishop"),
        positions=[pos(3, 1), pos(3, 3), pos(3, 3), pos(3, 5)],
    )
    state["pieces"]["1"]["rest_remaining"] = 5
    groups = blocker_groups(state, "0", pos(3, 5))
    assert groups == [
        {"position": pos(3, 3), "members": ["1", "2"], "path_index": 2, "endpoint": False},
        {"position": pos(3, 5), "members": ["3"], "path_index": 4, "endpoint": True},
    ]


def test_rest_snapshot_blocks_until_following_turn_and_objects_remain_allowed():
    state = world(types=("rook",), positions=[pos(0, 0)])
    commit_move(state, "0", pos(0, 2))
    assert state["pieces"]["0"]["rest_remaining"] == 2
    for remaining in [1, 0]:
        begin_turn(state)
        assert state["pieces"]["0"]["rest_remaining"] == remaining
        with pytest.raises(RuleError) as error:
            legal_move(state, "0", pos(0, 3))
        assert error.value.code == "PIECE_RESTING"
        apply_primary(state, "0", {"kind": "change_color", "label": "蓝"})
    begin_turn(state)
    legal_move(state, "0", pos(0, 3))


def test_knight_rest_is_chebyshev_after_success_zero_after_no_displacement():
    state = world(types=("knight",), positions=[pos(1, 1)])
    commit_move(state, "0", pos(1, 1))
    assert state["pieces"]["0"]["rest_remaining"] == 0
    commit_move(state, "0", pos(3, 2))
    for remaining in [1, 0]:
        begin_turn(state)
        assert state["pieces"]["0"]["rest_remaining"] == remaining
        with pytest.raises(RuleError):
            legal_move(state, "0", pos(1, 1))
    begin_turn(state)
    legal_move(state, "0", pos(1, 1))


def test_partial_slide_rest_uses_actual_displacement():
    state = world(types=("rook",), positions=[pos(3, 1)])
    commit_move(state, "0", pos(3, 3))
    assert state["pieces"]["0"]["rest_remaining"] == 2


def test_visibility_is_clipped_and_unoccluded():
    state = world(types=("rook", "knight"), positions=[pos(0, 0), pos(7, 7)])
    assert visible_positions(state, "0") == [pos(0, 0), pos(0, 1), pos(1, 0), pos(1, 1)]
    assert len(visible_positions(state, "1")) == 4


def test_negotiation_uses_geometry_only_and_aggregates_any_edge():
    state = world(types=("rook", "pawn", "bishop"), positions=[pos(3, 1), pos(3, 3), pos(5, 5)])
    state["pieces"]["0"]["rest_remaining"] = 9
    assert negotiation_mode(state, ["0"], ["1"]) == "a_advantage"
    assert negotiation_mode(state, ["0"], ["1"], endpoint=True) == "equal"
    assert negotiation_mode(state, ["1"], ["2"]) == "b_advantage"
    state["pieces"]["2"]["position"] = pos(6, 5)
    assert negotiation_mode(state, ["1"], ["2"]) == "equal"


def test_pickup_transfer_rejection_acceptance_and_drop_are_unique():
    state = world(types=("rook", "pawn"), positions=[pos(3, 1), pos(3, 2)])
    apply_primary(state, "0", {"kind": "pickup", "object_id": "seal"})
    offer = create_transfer_offer(state, "0", "seal", "1")
    assert state["objects"]["seal"]["location"] == {"kind": "carried", "holder_id": "0"}
    events = resolve_transfer(state, offer, False)
    assert events[0]["type"] == "ObjectTransferRejected"
    assert resolve_transfer(state, offer, True) == []
    assert state["objects"]["seal"]["location"]["holder_id"] == "0"
    second = create_transfer_offer(state, "0", "seal", "1")
    resolve_transfer(state, second, True)
    assert resolve_transfer(state, second, True) == []
    apply_primary(state, "1", {"kind": "drop", "object_id": "seal"})
    assert state["objects"]["seal"]["location"] == {"kind": "ground", "position": pos(3, 2)}
    assert_invariants(state)


def test_transfer_cancels_when_permission_changes():
    state = world(types=("rook", "pawn"), positions=[pos(3, 1), pos(3, 2)])
    apply_primary(state, "0", {"kind": "pickup", "object_id": "seal"})
    offer = create_transfer_offer(state, "0", "seal", "1")
    state["pieces"]["1"]["position"] = pos(7, 7)
    assert resolve_transfer(state, offer, True)[0]["type"] == "OfferCancelled"
    assert state["objects"]["seal"]["location"]["holder_id"] == "0"
    assert_invariants(state)


def test_physical_join_only_no_reset_even_resting_at_anchor():
    state = world()
    b = battle(state, "b", ["0", "1"])
    before = deepcopy(state)
    with pytest.raises(RuleError) as error:
        authorize_join(state, "2", "b")
    assert error.value.code == "BATTLE_NOT_AT_ANCHOR"
    assert state == before
    state["pieces"]["2"]["position"] = pos(3, 3)
    state["pieces"]["2"]["rest_remaining"] = 5
    authorize_join(state, "2", "b")
    assert b["round"] == 1
    assert b["max_rounds"] == 4
    assert authorize_join(state, "2", "b") == []
    assert "2" in state["locked_this_turn"]
    assert_invariants(state)


def test_join_rejects_settling_or_other_battle():
    state = world()
    battle(state, "b", ["0", "1"])
    battle(state, "other", ["2", "3"])
    state["pieces"]["2"]["position"] = pos(3, 3)
    with pytest.raises(RuleError) as error:
        authorize_join(state, "2", "b")
    assert error.value.code == "BATTLE_ALREADY_ACTIVE"
    state["battles"]["b"]["status"] = "settling"
    with pytest.raises(RuleError) as error:
        authorize_join(state, "2", "b")
    assert error.value.code == "BATTLE_NOT_ACTIVE"


@pytest.mark.parametrize(
    "winners,losers,surrendered",
    [
        ([], ["0", "1"], []),
        (["0"], [], []),
        (["0"], ["0", "1"], []),
        (["0"], ["1"], ["0"]),
        (["0"], ["2"], []),
        (["0", "0"], ["1"], []),
    ],
)
def test_battle_partition_rejects_empty_neutral_overlap_or_surrendering_winner(
    winners, losers, surrendered
):
    with pytest.raises(RuleError) as error:
        validate_battle_partition(["0", "1"], winners, losers, surrendered)
    assert error.value.code == "BATTLE_PARTITION_INVALID"


def test_release_requires_every_living_winner_while_any_winner_may_execute():
    state = settling_world()
    for approvals in [{"0": True}, {"0": True, "2": False}, {"0": True, "2": True, "3": True}]:
        before = deepcopy(state)
        with pytest.raises(RuleError) as error:
            release_loser(state, "b", "0", "1", approvals)
        assert error.value.code == "RELEASE_NOT_UNANIMOUS"
        assert state == before
    release_loser(state, "b", "0", "1", {"0": True, "2": True})
    assert state["pieces"]["1"]["alive"]
    assert state["pieces"]["1"]["battle_id"] is None
    assert release_loser(state, "b", "0", "1", {"0": True, "2": True}) == []
    assert_invariants(state)


def test_execution_atomically_drops_objects_removes_membership_and_creates_private_safe_tombstone():
    state = settling_world()
    state["objects"]["seal"]["location"] = {"kind": "carried", "holder_id": "1"}
    drafts = kill_loser(state, "b", "2", "1")
    assert not state["pieces"]["1"]["alive"]
    assert state["pieces"]["1"]["battle_id"] is None
    assert "1" not in occupants(state, pos(3, 3))
    assert state["objects"]["seal"]["location"] == {"kind": "ground", "position": pos(3, 3)}
    tomb = next(iter(state["tombstones"].values()))
    assert set(tomb) == {"id", "piece_id", "position", "death_event_id"}
    assert {draft["type"] for draft in drafts} >= {
        "DeathCommitted",
        "ObjectDroppedByDeath",
        "MembershipChanged",
    }
    assert kill_loser(state, "b", "2", "1") == []
    assert_invariants(state)


def test_invariants_require_durable_unanimous_release_authorization():
    state = settling_world()
    release_loser(state, "b", "0", "1", {"0": True, "2": True})
    state["battles"]["b"]["release_approvals"]["1"] = ["0"]
    with pytest.raises(RuleError) as error:
        assert_invariants(state)
    assert error.value.code == "INVARIANT_VIOLATION"


def test_unauthorized_execution_rejects_without_mutation():
    state = settling_world()
    before = deepcopy(state)
    with pytest.raises(RuleError) as error:
        kill_loser(state, "b", "3", "1")
    assert error.value.code == "SETTLEMENT_UNAUTHORIZED"
    assert state == before


def test_true_battle_merge_recomputes_rounds_and_preserves_history():
    state = world()
    a = battle(state, "a", ["0", "1"])
    b = battle(state, "b", ["2", "3"])
    a["round"] = 3
    a["narratives"] = [{"text": "parent A"}]
    b["round"] = 2
    state["turn"] = 7
    state["limits"]["battle_max_rounds"] = 5
    bid, drafts = merge_battles(state, ["a", "b"], pos(3, 3))
    merged = state["battles"][bid]
    assert merged["round"] == 0 and merged["max_rounds"] == 5
    assert merged["created_turn"] == 7
    assert merged["parent_history"][0]["narratives"] == [{"text": "parent A"}]
    assert a["status"] == b["status"] == "merged"
    assert all(p["battle_id"] == bid for p in state["pieces"].values())
    assert drafts[0]["type"] == "BattlesMerged"
    assert_invariants(state)


def test_different_anchor_merge_is_rejected_without_mutation():
    state = world()
    battle(state, "a", ["0", "1"])
    battle(state, "b", ["2", "3"], anchor=pos(4, 4))
    before = deepcopy(state)
    with pytest.raises(RuleError):
        merge_battles(state, ["a", "b"], pos(3, 3))
    assert state == before


def test_invariants_reject_double_location_and_dead_holder():
    state = world()
    state["objects"]["seal"]["location"]["holder_id"] = "0"
    with pytest.raises(RuleError):
        assert_invariants(state)
    state["objects"]["seal"]["location"] = {"kind": "carried", "holder_id": "0"}
    state["pieces"]["0"]["alive"] = False
    with pytest.raises(RuleError):
        assert_invariants(state)
