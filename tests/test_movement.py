"""Deterministic protocol tests, including restart through JSON cursor copies."""

import json
from copy import deepcopy

import pytest
from chessx.domain.errors import RuleError
from chessx.domain.models import Scenario
from chessx.domain.scenarios import create_world
from chessx.rules.invariants import assert_invariants
from chessx.runtime.movement import MovementMixin
from jsonschema import validate


def pos(row, col):
    return {"row": row, "col": col}


def world(types=("rook", "rook"), positions=None):
    positions = positions or [pos(3, 1), pos(3, 3)]
    scenario = Scenario.model_validate(
        {
            "scenario_id": "movement",
            "title": "Movement fixture",
            "board": {"rows": 8, "cols": 8},
            "pieces": [
                {
                    "id": str(i),
                    "name": f"P{i}",
                    "type": kind,
                    "color": "白",
                    "position": positions[i],
                }
                for i, kind in enumerate(types)
            ],
        }
    )
    return create_world(scenario, {}, "w")


def start(state, target, arrival=None):
    intent = {"kind": "move", "target": target}
    if arrival:
        intent["arrival_action"] = {"kind": "join_battle", "battle_id": arrival}
    state["turn"] = 1
    state["cursor"] = {
        "actor_index": 0,
        "work": {"kind": "move", "intent": intent, "stage": "init", "actor_id": "0"},
    }


class Harness(MovementMixin):
    def __init__(self, replies=()):
        self.replies = iter(replies)
        self.calls = []

    async def ask(self, state, pid, stage, slot, schema, protocol, validator=None):
        self.calls.append((pid, stage, slot, deepcopy(protocol)))
        reply = next(self.replies)
        validate(reply, schema)
        if validator:
            validator(reply)
        return reply

    def finish_primary(self, state):
        state["cursor"]["work"] = None
        state["cursor"]["actor_index"] += 1

    async def run(self, state, restart_each_step=False):
        events = []
        for _ in range(200):
            work = state["cursor"]["work"]
            if work is None:
                return state, events
            before_calls = len(self.calls)
            events.extend(await self.move_step(state, work))
            assert len(self.calls) - before_calls <= 1
            assert_invariants(state)
            if restart_each_step:
                state = json.loads(json.dumps(state))
        pytest.fail("Movement did not reach a finite terminal commit")


@pytest.mark.asyncio
async def test_unblocked_move_and_zero_move_require_no_model():
    state = world(types=("rook",), positions=[pos(1, 1)])
    start(state, pos(1, 5))
    runner = Harness()
    state, events = await runner.run(state, restart_each_step=True)
    assert state["pieces"]["0"]["position"] == pos(1, 5)
    assert state["pieces"]["0"]["rest_remaining"] == 4
    assert not runner.calls
    assert [e["type"] for e in events] == ["MoveProposed", "MoveCommitted"]
    start(state, pos(1, 5))
    state["pieces"]["0"]["rest_remaining"] = 0
    state, events = await runner.run(state)
    assert [e["type"] for e in events] == ["PieceWaited"]


@pytest.mark.asyncio
async def test_equal_stop_keeps_pending_origin_until_atomic_partial_commit():
    state = world()
    start(state, pos(3, 5))
    runner = Harness(
        [
            {"kind": "say", "speech": "停下。", "proposal": "stop"},
            {"kind": "say", "speech": "同意。", "proposal": "stop"},
        ]
    )
    while state["cursor"]["work"]["stage"] != "commit":
        await runner.move_step(state, state["cursor"]["work"])
        assert state["pieces"]["0"]["position"] == pos(3, 1)
    await runner.move_step(state, state["cursor"]["work"])
    assert state["pieces"]["0"]["position"] == pos(3, 2)
    assert state["pieces"]["0"]["rest_remaining"] == 1
    assert len(runner.calls) == 2


@pytest.mark.asyncio
async def test_single_advantage_decides_without_disadvantaged_veto():
    state = world(types=("rook", "pawn"))
    start(state, pos(3, 5))
    runner = Harness([{"kind": "say", "speech": "请让我通过。", "proposal": "pass"}])
    state, events = await runner.run(state)
    assert len(runner.calls) == 1
    assert state["pieces"]["0"]["position"] == pos(3, 5)
    assert not state["battles"]
    assert (
        next(e for e in events if e["type"] == "NegotiationResolved")["payload"]["result"] == "pass"
    )


@pytest.mark.asyncio
async def test_endpoint_has_only_one_equal_protocol_and_return_is_origin():
    state = world(types=("rook", "pawn"))
    start(state, pos(3, 3))
    runner = Harness(
        [
            {"kind": "say", "speech": "我回去。", "proposal": "return_mover"},
            {"kind": "pass", "proposal": "return_mover"},
        ]
    )
    state, events = await runner.run(state)
    assert state["pieces"]["0"]["position"] == pos(3, 1)
    assert state["pieces"]["1"]["position"] == pos(3, 3)
    assert state["pieces"]["0"]["rest_remaining"] == 0
    assert len([e for e in events if e["type"] == "ConversationCreated"]) == 1
    assert runner.calls[0][3]["mode"] == "equal"
    assert runner.calls[0][3]["allowed_results"] == ["coexist", "return_mover", "fight"]


@pytest.mark.asyncio
async def test_multi_occupant_requires_each_voice_and_never_reveals_later_group():
    state = world(
        types=("rook", "rook", "rook", "rook"),
        positions=[pos(3, 1), pos(3, 3), pos(3, 3), pos(3, 5)],
    )
    state["pieces"]["1"]["rest_remaining"] = 4
    start(state, pos(3, 6))
    replies = [{"kind": "pass", "proposal": "pass"}] * 5
    runner = Harness(replies)
    state, events = await runner.run(state, restart_each_step=True)
    assert [c[0] for c in runner.calls] == ["0", "1", "2", "0", "3"]
    assert "P3" not in json.dumps(runner.calls[0][3])
    assert state["pieces"]["0"]["position"] == pos(3, 6)
    assert len([e for e in events if e["type"] == "ConversationCreated"]) == 2


@pytest.mark.asyncio
async def test_two_idle_cycles_fight_with_original_positions_and_bounded_notice():
    state = world(types=("rook", "rook", "pawn"), positions=[pos(3, 1), pos(3, 3), pos(7, 7)])
    start(state, pos(3, 5))
    runner = Harness([{"kind": "pass"}] * 4)
    state, events = await runner.run(state)
    b = next(iter(state["battles"].values()))
    assert b["members"] == ["0", "1"]
    assert b["round"] == 0 and b["created_turn"] == 1
    assert state["pieces"]["0"]["position"] == pos(3, 2)
    assert state["pieces"]["1"]["position"] == pos(3, 3)
    notice = next(e for e in events if e["type"] == "BattleNotice")
    assert notice["audience"] == ["0", "1"]
    assert "3" not in notice["payload"]["participant_ids"]
    assert len(runner.calls) == 4


@pytest.mark.asyncio
async def test_final_fiftieth_message_checks_valid_agreement_before_limit():
    state = world()
    start(state, pos(3, 5))
    runner = Harness(
        [{"kind": "say", "speech": "考虑中。", "proposal": "undecided"}] * 48
        + [
            {"kind": "say", "speech": "停下。", "proposal": "stop"},
            {"kind": "say", "speech": "同意。", "proposal": "stop"},
        ]
    )
    state, events = await runner.run(state, restart_each_step=True)
    conv = next(iter(state["conversations"].values()))
    assert conv["message_count"] == 50
    assert len(set(conv["counted_message_ids"])) == 50
    assert len(runner.calls) == 50
    assert state["pieces"]["0"]["position"] == pos(3, 2)
    assert not state["battles"]
    assert (
        next(e for e in events if e["type"] == "NegotiationResolved")["payload"]["result"] == "stop"
    )


@pytest.mark.asyncio
async def test_two_person_hundredth_without_agreement_fights_no_extra_call():
    state = world()
    start(state, pos(3, 5))
    runner = Harness([{"kind": "say", "speech": "尚未决定。"}] * 100)
    state, _ = await runner.run(state)
    assert len(runner.calls) == 100
    assert next(iter(state["conversations"].values()))["message_count"] == 100
    assert len(state["battles"]) == 1


@pytest.mark.asyncio
async def test_knight_fight_stays_at_origin_with_zero_rest():
    state = world(types=("knight", "rook"), positions=[pos(1, 1), pos(3, 2)])
    start(state, pos(3, 2))
    runner = Harness([{"kind": "leave"}])
    state, _ = await runner.run(state)
    assert state["pieces"]["0"]["position"] == pos(1, 1)
    assert state["pieces"]["0"]["rest_remaining"] == 0


def active_battle(state, members, anchor):
    state["battles"]["b"] = {
        "id": "b",
        "status": "active",
        "anchor": anchor,
        "members": list(members),
        "round": 2,
        "max_rounds": 4,
        "created_turn": 0,
        "narratives": [],
    }
    for pid in members:
        state["pieces"][pid]["battle_id"] = "b"


@pytest.mark.asyncio
async def test_successful_arrival_skips_endpoint_voting_and_preserves_rounds():
    state = world(types=("rook", "rook", "rook"), positions=[pos(3, 1), pos(3, 3), pos(4, 3)])
    active_battle(state, ["1", "2"], pos(3, 3))
    start(state, pos(3, 3), arrival="b")
    runner = Harness()
    state, events = await runner.run(state)
    assert not runner.calls
    assert state["pieces"]["0"]["position"] == pos(3, 3)
    assert state["pieces"]["0"]["battle_id"] == "b"
    assert state["battles"]["b"]["round"] == 2
    assert state["pieces"]["0"]["rest_remaining"] == 2
    assert "BattleJoined" in [e["type"] for e in events]


@pytest.mark.asyncio
async def test_intermediate_stop_cancels_arrival_without_receiving_battle_history():
    state = world(
        types=("rook", "rook", "rook", "rook"),
        positions=[pos(3, 1), pos(3, 3), pos(3, 5), pos(4, 5)],
    )
    active_battle(state, ["2", "3"], pos(3, 5))
    start(state, pos(3, 5), arrival="b")
    runner = Harness([{"kind": "pass", "proposal": "stop"}] * 2)
    state, events = await runner.run(state)
    assert state["pieces"]["0"]["position"] == pos(3, 2)
    assert state["pieces"]["0"]["battle_id"] is None
    assert "BattleJoined" not in [e["type"] for e in events]
    assert "P2" not in json.dumps(runner.calls[0][3])


@pytest.mark.asyncio
async def test_active_member_non_anchor_silence_stops_no_second_battle():
    state = world(types=("rook", "rook", "rook"), positions=[pos(3, 1), pos(3, 3), pos(4, 4)])
    active_battle(state, ["1", "2"], pos(4, 4))
    start(state, pos(3, 5))
    runner = Harness([{"kind": "pass"}] * 4)
    state, _ = await runner.run(state)
    assert runner.calls[0][3]["allowed_results"] == ["pass", "stop"]
    assert len(state["battles"]) == 1
    assert state["pieces"]["0"]["position"] == pos(3, 2)
    assert state["pieces"]["0"]["battle_id"] is None


@pytest.mark.asyncio
async def test_model_failure_keeps_same_permission_cursor_and_zero_dialogue():
    state = world()
    start(state, pos(3, 5))
    runner = Harness([{"kind": "say", "speech": "   "}])
    await runner.move_step(state, state["cursor"]["work"])
    await runner.move_step(state, state["cursor"]["work"])
    before = deepcopy(state)
    with pytest.raises(RuleError):
        await runner.move_step(state, state["cursor"]["work"])
    assert state == before
    assert next(iter(state["conversations"].values()))["message_count"] == 0


@pytest.mark.asyncio
async def test_pawn_anchor_marker_without_live_occupant_cannot_grant_diagonal_move():
    state = world(types=("pawn", "rook", "rook"), positions=[pos(3, 3), pos(4, 3), pos(5, 4)])
    active_battle(state, ["1", "2"], pos(4, 4))
    start(state, pos(4, 4), arrival="b")
    runner = Harness()
    with pytest.raises(RuleError) as error:
        await runner.move_step(state, state["cursor"]["work"])
    assert error.value.code == "MOVE_GEOMETRY_INVALID"
    assert not runner.calls
