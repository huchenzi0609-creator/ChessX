"""Actual runner/store protocol tests using explicitly scripted engineering models."""

import asyncio
from collections import deque
from copy import deepcopy

import pytest
from chessx.config import Settings
from chessx.domain.models import Scenario
from chessx.domain.scenarios import create_world
from chessx.llm import FixtureAdapter, ModelError
from chessx.llm.interface import ModelResult
from chessx.runtime.runner import WorldRunner
from chessx.storage.store import Store, digest
from jsonschema import ValidationError as SchemaError
from jsonschema import validate


def pos(row, col):
    return {"row": row, "col": col}


class ScriptedAdapter(FixtureAdapter):
    """Fixed formal outputs with known exact usage; never reads environment or network."""

    def __init__(self, answers=None):
        super().__init__()
        self.answers = {key: deque(values) for key, values in (answers or {}).items()}
        self.calls = []

    async def generate(self, stage, context, schema, *, max_output_tokens=512):
        pid = context.get("actor_id")
        self.calls.append((stage, pid, deepcopy(context.get("protocol", {}))))
        candidates = [(stage, pid), stage]
        answer = None
        for key in candidates:
            if self.answers.get(key):
                answer = self.answers[key].popleft()
                break
        if isinstance(answer, Exception):
            raise answer
        if answer is None:
            answer = (
                {"kind": "wait"}
                if stage == "action"
                else {"kind": "pass"}
                if stage == "ordinary"
                else self._choose(stage, context)
            )
        try:
            validate(answer, schema)
        except SchemaError as error:
            raise ModelError(
                "SCRIPT_SCHEMA_INVALID", "The engineering response violated its contract"
            ) from error
        return ModelResult(deepcopy(answer), "ENGINEERING_FIXTURE_TRACE", 10, 11, 12, 0)


def initial_state(count=3):
    positions = [pos(3, 1), pos(3, 2), pos(3, 3), pos(4, 3)]
    scenario = Scenario.model_validate(
        {
            "scenario_id": "integration",
            "title": "Engineering combat fixture",
            "board": {"rows": 8, "cols": 8},
            "pieces": [
                {
                    "id": str(i),
                    "name": f"P{i}",
                    "type": "rook",
                    "color": "白",
                    "position": positions[i],
                    "private_text": f"Persona {i}",
                }
                for i in range(count)
            ],
            "objects": [
                {
                    "id": "seal",
                    "name": "印信",
                    "description": "铜制印信",
                    "location": {"kind": "carried", "holder_id": str(count - 1)},
                }
            ],
        }
    )
    state = create_world(scenario, {}, "integration-world")
    state["mode"] = "fixture"
    return state


def set_battle(
    state, *, settling=False, winners=None, losers=None, members=None, bid="b", anchor=None
):
    members = members or list(state["pieces"])
    battle = {
        "id": bid,
        "status": "settling" if settling else "active",
        "members": list(members),
        "anchor": anchor or pos(3, 2),
        "round": 1 if settling else 0,
        "max_rounds": 4,
        "created_turn": 0,
        "narratives": [],
        "public_narratives": [],
        "surrendered": [],
    }
    if settling:
        battle.update(
            combat_participants=list(members),
            winner_ids=winners or ["0", "1"],
            loser_ids=losers or ["2"],
            settlements={},
        )
        state.update(turn=1, phase="COMBAT", locked_this_turn=list(members))
        state["cursor"] = {
            "battle_queue": [bid],
            "battle_index": 0,
            "work": {"battle_id": bid, "stage": "settlement_init", "loser_index": 0},
        }
    state["battles"][bid] = battle
    for pid in members:
        state["pieces"][pid]["battle_id"] = bid
    return battle


@pytest.fixture
def setup(tmp_path):
    stores = []

    def make(state, answers=None):
        store = Store(tmp_path / f"test-{len(stores)}.sqlite3")
        stores.append(store)
        store.create(state)
        runner = WorldRunner(store, Settings(data_dir=tmp_path))
        adapter = ScriptedAdapter(answers)
        runner.adapters[state["world_id"]] = adapter
        return store, runner, adapter

    yield make
    for store in stores:
        store.close()


async def run_segment(store, runner, command_id, turns=1):
    await runner.start(
        "integration-world", {"kind": "run", "command_id": command_id, "turns": turns}
    )
    await runner.tasks["integration-world"]
    state = store.get("integration-world")
    assert state.get("error") is None, state.get("error")
    assert state["lifecycle"] == "PAUSED_SEGMENT"
    return state


async def until(store, runner, predicate, maximum=300):
    for _ in range(maximum):
        state = store.get("integration-world")
        if predicate(state):
            return state
        state = await runner.step("integration-world")
        assert state.get("error") is None, state.get("error")
    pytest.fail("The durable protocol failed to reach the requested cursor")


def all_events(store):
    return store.events("integration-world", limit=10000)["events"]


@pytest.mark.asyncio
async def test_battle_advances_once_each_world_turn_and_first_surrender_preserves_unasked(setup):
    state = initial_state()
    set_battle(state)
    store, runner, adapter = setup(
        state,
        {
            "surrender": [{"surrender": False}] * 4 + [{"surrender": True}],
            "outcome": [{"winners": ["0", "2"], "losers": ["1"]}],
        },
    )
    first = await run_segment(store, runner, "first")
    assert first["completed_turn"] == 1
    assert first["battles"]["b"]["round"] == 1
    assert first["battles"]["b"]["status"] == "active"
    second = await run_segment(store, runner, "second")
    b = second["battles"]["b"]
    assert second["completed_turn"] == 2 and b["round"] == 2
    assert b["status"] == "closed"
    assert b["winner_ids"] == ["0", "2"] and b["loser_ids"] == ["1"]
    assert b["round_decisions"]["2"] == {"0": "continue", "1": "surrender", "2": "not_asked"}
    assert [(stage, pid) for stage, pid, _ in adapter.calls if stage == "surrender"] == [
        ("surrender", "0"),
        ("surrender", "1"),
        ("surrender", "2"),
        ("surrender", "0"),
        ("surrender", "1"),
    ]
    assert [e["turn"] for e in all_events(store) if e["type"] == "BattleNarrated"] == [1, 2]
    for pid in ("0", "1", "2"):
        receipt = next(
            r for r in second["receipts"][pid] if r["channel"] == "BattleOutcomeCommitted"
        )
        assert receipt["payload"]["payload"]["winner_ids"] == ["0", "2"]
        assert receipt["payload"]["payload"]["loser_ids"] == ["1"]
    assert second["usage"] == {
        "calls": len(adapter.calls),
        "input_tokens": 10 * len(adapter.calls),
        "output_tokens": 11 * len(adapter.calls),
        "reasoning_tokens": 12 * len(adapter.calls),
    }


@pytest.mark.asyncio
async def test_release_reject_returns_to_same_message_budget_then_all_winners_approve(setup):
    state = initial_state()
    set_battle(state, settling=True)
    store, runner, adapter = setup(
        state,
        {
            "settlement": [
                {"kind": "propose_release", "speech": "先释放吧。"},
                {"kind": "say", "speech": "我们再讨论条件。"},
                {"kind": "pass"},
                {"kind": "propose_release"},
            ],
            "release_vote": [{"approve": False}, {"approve": True}],
        },
    )
    done = await run_segment(store, runner, "run")
    events = all_events(store)
    kinds = [e["type"] for e in events]
    assert kinds.count("ReleaseProposed") == 2
    assert kinds.count("ReleaseProposalRejected") == 1
    assert kinds.count("LoserReleased") == 1
    assert kinds.index("ReleaseProposalRejected") < kinds.index("LoserReleased")
    settlement = next(c for c in done["conversations"].values() if c["kind"] == "settlement")
    assert settlement["message_count"] == 2
    assert done["battles"]["b"]["release_approvals"]["2"] == ["0", "1"]
    assert len([c for c in adapter.calls if c[0] == "release_vote"]) == 2
    rejected_vote = next(
        r
        for r in done["receipts"]["0"]
        if r["channel"] == "ReleaseVoteCast" and r["payload"]["payload"].get("approve") is False
    )
    assert rejected_vote["payload"]["payload"]["loser_id"] == "2"
    assert (
        rejected_vote["payload"].get(
            "actor_id", rejected_vote["payload"]["payload"].get("actor_id")
        )
        == "1"
    )


@pytest.mark.asyncio
async def test_three_winner_release_reject_stops_vote_requests_immediately(setup):
    state = initial_state(4)
    set_battle(state, settling=True, winners=["0", "1", "2"], losers=["3"])
    store, runner, adapter = setup(
        state,
        {
            "settlement": [{"kind": "propose_release"}] + [{"kind": "pass"}] * 8,
            "release_vote": [{"approve": False}],
            "settlement_terminal": [{"kind": "approve_release"}] * 3,
        },
    )
    done = await run_segment(store, runner, "run")
    assert [(stage, pid) for stage, pid, _ in adapter.calls if stage == "release_vote"] == [
        ("release_vote", "1")
    ]
    assert done["battles"]["b"]["settlements"]["3"] == "released"
    assert len([c for c in adapter.calls if c[0] == "settlement_terminal"]) == 3


@pytest.mark.asyncio
async def test_terminal_votes_are_reset_for_each_loser_and_exactly_all_winners_respond(setup):
    state = initial_state(4)
    set_battle(state, settling=True, winners=["0", "1"], losers=["2", "3"])
    store, runner, adapter = setup(
        state,
        {
            "settlement": [{"kind": "pass"}] * 12,
            "settlement_terminal": [{"kind": "approve_release"}] * 4,
        },
    )
    done = await run_segment(store, runner, "run")
    calls = [
        (pid, protocol["loser_id"])
        for stage, pid, protocol in adapter.calls
        if stage == "settlement_terminal"
    ]
    assert calls == [("0", "2"), ("1", "2"), ("0", "3"), ("1", "3")]
    assert done["battles"]["b"]["settlements"] == {"2": "released", "3": "released"}
    assert all(p["alive"] and p["battle_id"] is None for p in done["pieces"].values())


@pytest.mark.asyncio
async def test_terminal_any_winner_execution_atomically_drops_inventory_and_binds_tombstone(setup):
    state = initial_state()
    set_battle(state, settling=True)
    store, runner, adapter = setup(
        state,
        {
            "settlement": [{"kind": "pass"}] * 6,
            "settlement_terminal": [{"kind": "approve_release"}, {"kind": "execute_loser"}],
        },
    )
    done = await run_segment(store, runner, "run")
    assert not done["pieces"]["2"]["alive"]
    assert done["pieces"]["2"]["battle_id"] is None
    assert done["objects"]["seal"]["location"] == {"kind": "ground", "position": pos(3, 3)}
    assert done["battles"]["b"]["settlements"] == {"2": "executed"}
    tomb = next(iter(done["tombstones"].values()))
    death = next(e for e in all_events(store) if e["type"] == "DeathCommitted")
    assert tomb["death_event_id"] == death["event_id"]
    assert not ({"killer", "killer_id", "actor_id"} & set(tomb))
    assert len([c for c in adapter.calls if c[0] == "settlement_terminal"]) == 2
    assert digest(store.replay("integration-world", done["seq"])) == digest(done)


@pytest.mark.asyncio
async def test_settlement_object_transfer_can_reject_then_accept_without_loser_primary_action(
    setup,
):
    state = initial_state()
    state["pieces"]["0"]["position"] = pos(3, 2)
    set_battle(state, settling=True)
    replies = [
        {"kind": "say", "speech": "请交给我。"},
        {"kind": "pass"},
        {
            "kind": "accept_condition",
            "intent": {"kind": "transfer", "object_id": "seal", "target_id": "0"},
        },
        {"kind": "pass"},
        {"kind": "pass"},
        {
            "kind": "accept_condition",
            "intent": {"kind": "transfer", "object_id": "seal", "target_id": "0"},
        },
        {"kind": "approve_release"},
    ]
    store, runner, _ = setup(
        state, {"settlement": replies, "accept_transfer": [{"accept": False}, {"accept": True}]}
    )
    done = await run_segment(store, runner, "run")
    kinds = [e["type"] for e in all_events(store)]
    assert kinds.count("ObjectTransferOffered") == 2
    assert kinds.count("ObjectTransferRejected") == kinds.count("ObjectTransferred") == 1
    assert done["objects"]["seal"]["location"] == {"kind": "carried", "holder_id": "0"}
    assert done["battles"]["b"]["settlements"]["2"] == "released"
    assert not any(e["type"] == "ActionChosen" and e["actor_id"] == "2" for e in all_events(store))


@pytest.mark.asyncio
async def test_restart_at_partial_terminal_votes_keeps_vote_and_requests_only_remaining_winner(
    setup, tmp_path
):
    state = initial_state()
    set_battle(state, settling=True)
    state.update(
        lifecycle="RUNNING",
        target_turn=1,
        segments=[{"id": "seed", "started_seq": 1, "target_turn": 1}],
    )
    store, runner, _ = setup(
        state,
        {
            "settlement": [{"kind": "pass"}] * 6,
            "settlement_terminal": [{"kind": "approve_release"}],
        },
    )
    frozen = await until(
        store, runner, lambda s: s["cursor"].get("work", {}).get("terminal_votes") == {"0": True}
    )
    cursor_before = deepcopy(frozen["cursor"])
    # An uncommitted attempt simulates process death during the next request.
    store.prepare(
        "integration-world",
        "battle:b:loser:2:terminal:1",
        frozen["generation"],
        {"actor_id": "1", "fixture": True},
    )
    new_runner = WorldRunner(store, Settings(data_dir=tmp_path))
    adapter = ScriptedAdapter({"settlement_terminal": [{"kind": "approve_release"}]})
    new_runner.adapters["integration-world"] = adapter
    new_runner.recover()
    recovered = store.get("integration-world")
    assert recovered["cursor"] == cursor_before
    assert recovered["generation"] == frozen["generation"] + 1
    assert recovered["lifecycle"] == "PAUSED"
    pending = store.db.execute(
        "SELECT status FROM operations ORDER BY rowid DESC LIMIT 1"
    ).fetchone()[0]
    assert pending == "obsolete"
    done = await run_segment(store, new_runner, "resumed")
    assert done["battles"]["b"]["settlements"]["2"] == "released"
    assert [(stage, pid) for stage, pid, _ in adapter.calls if stage == "settlement_terminal"] == [
        ("settlement_terminal", "1")
    ]


@pytest.mark.asyncio
async def test_actual_runner_move_arrival_joins_before_this_turn_narration_and_does_not_reset(
    setup,
):
    state = initial_state()
    state["pieces"]["0"]["position"] = pos(3, 0)
    state["pieces"]["1"]["position"] = pos(3, 3)
    state["pieces"]["2"]["position"] = pos(4, 3)
    b = set_battle(state, members=["1", "2"], anchor=pos(3, 3))
    b["round"] = 1
    store, runner, _ = setup(
        state,
        {
            ("action", "0"): [
                {
                    "kind": "move",
                    "target": pos(3, 3),
                    "arrival_action": {"kind": "join_battle", "battle_id": "b"},
                }
            ]
        },
    )
    done = await run_segment(store, runner, "run")
    assert done["pieces"]["0"]["position"] == pos(3, 3)
    assert done["pieces"]["0"]["battle_id"] == "b"
    assert done["battles"]["b"]["round"] == 2
    events = all_events(store)
    assert next(e["seq"] for e in events if e["type"] == "BattleJoined") < next(
        e["seq"] for e in events if e["type"] == "BattleNarrated"
    )


@pytest.mark.asyncio
async def test_actual_runner_remote_join_errors_and_keeps_action_slot_without_model_fallback(setup):
    state = initial_state()
    state["pieces"]["0"]["position"] = pos(2, 2)
    set_battle(state, members=["1", "2"], anchor=pos(3, 3))
    store, runner, adapter = setup(
        state, {("action", "0"): [{"kind": "join_battle", "battle_id": "b"}] * 3}
    )
    await runner.start("integration-world", {"kind": "run", "command_id": "remote", "turns": 1})
    await runner.tasks["integration-world"]
    stopped = store.get("integration-world")
    assert stopped["lifecycle"] == "PAUSED_ERROR"
    assert stopped["error"]["code"] == "BATTLE_NOT_AT_ANCHOR"
    assert stopped["pieces"]["0"]["battle_id"] is None
    assert stopped["cursor"]["actor_index"] == 0 and stopped["cursor"]["work"] is None
    assert len([call for call in adapter.calls if call[0] == "action"]) == 3
    assert not any(e["type"] in {"BattleJoined", "ActionChosen"} for e in all_events(store))


@pytest.mark.asyncio
async def test_wrong_loser_execution_is_rejected_without_death_or_speech_commit(setup):
    state = initial_state()
    set_battle(state, settling=True)
    store, runner, adapter = setup(
        state,
        {"settlement": [{"kind": "execute_loser", "loser_id": "1", "speech": "非法目标。"}] * 3},
    )
    await runner.start(
        "integration-world", {"kind": "run", "command_id": "wrong-loser", "turns": 1}
    )
    await runner.tasks["integration-world"]
    stopped = store.get("integration-world")
    assert stopped["error"]["code"] == "SETTLEMENT_WRONG_LOSER"
    assert all(piece["alive"] for piece in stopped["pieces"].values())
    assert not any(e["type"] in {"DeathCommitted", "SettlementMessage"} for e in all_events(store))
    assert len([call for call in adapter.calls if call[0] == "settlement"]) == 3


@pytest.mark.asyncio
async def test_new_battle_is_not_narrated_until_next_world_turn(setup):
    state = initial_state()
    store, runner, _ = setup(
        state,
        {
            ("action", "0"): [{"kind": "move", "target": pos(3, 5)}],
            "negotiation": [
                {"kind": "say", "speech": "交锋吧。", "proposal": "fight"},
                {"kind": "say", "speech": "同意交锋。", "proposal": "fight"},
            ],
        },
    )
    first = await run_segment(store, runner, "create-battle")
    battle = next(iter(first["battles"].values()))
    assert battle["created_turn"] == 1 and battle["round"] == 0
    assert not any(e["type"] == "BattleNarrated" for e in all_events(store))
    second = await run_segment(store, runner, "next-turn")
    assert second["battles"][battle["id"]]["round"] == 1
    assert [e["turn"] for e in all_events(store) if e["type"] == "BattleNarrated"] == [2]


@pytest.mark.asyncio
async def test_actual_runner_rejects_neutral_incomplete_outcome_without_advancing(setup):
    state = initial_state()
    set_battle(state)
    state.update(turn=1, phase="COMBAT")
    state["cursor"] = {
        "battle_queue": ["b"],
        "battle_index": 0,
        "work": {"battle_id": "b", "stage": "outcome"},
    }
    store, runner, _ = setup(state, {"outcome": [{"winners": ["0"], "losers": ["1"]}] * 3})
    await runner.start(
        "integration-world", {"kind": "run", "command_id": "bad-outcome", "turns": 1}
    )
    await runner.tasks["integration-world"]
    stopped = store.get("integration-world")
    assert stopped["error"]["code"] == "BATTLE_PARTITION_INVALID"
    assert stopped["battles"]["b"]["status"] == "active"
    assert stopped["cursor"]["work"]["stage"] == "outcome"
    assert not any(e["type"] == "BattleOutcomeCommitted" for e in all_events(store))


class LateNarratorAdapter(ScriptedAdapter):
    def __init__(self):
        super().__init__()
        self.entered = asyncio.Event()
        self.release = asyncio.Event()

    async def generate(self, stage, context, schema, *, max_output_tokens=512):
        if stage == "battle_narrator":
            self.entered.set()
            try:
                await self.release.wait()
            except asyncio.CancelledError:
                # Simulate a provider SDK returning despite caller cancellation.
                await self.release.wait()
        return await super().generate(stage, context, schema, max_output_tokens=max_output_tokens)


@pytest.mark.asyncio
async def test_pause_during_narration_prevents_late_result_commit_and_resume_runs_once(setup):
    state = initial_state()
    set_battle(state)
    store, runner, _ = setup(state)
    adapter = LateNarratorAdapter()
    runner.adapters["integration-world"] = adapter
    await runner.start(
        "integration-world", {"kind": "run", "command_id": "before-pause", "turns": 1}
    )
    try:
        await asyncio.wait_for(adapter.entered.wait(), timeout=2)
        before = store.get("integration-world")
        assert before["phase"] == "COMBAT"
        runner.pause("integration-world")
    finally:
        adapter.release.set()
    await asyncio.wait_for(runner.tasks["integration-world"], timeout=2)
    paused = store.get("integration-world")
    assert paused["generation"] == before["generation"] + 1
    assert paused["battles"]["b"]["round"] == 0
    assert paused["cursor"] == before["cursor"]
    assert not any(e["type"] == "BattleNarrated" for e in all_events(store))
    assert (
        store.db.execute("SELECT status FROM operations ORDER BY rowid DESC LIMIT 1").fetchone()[0]
        == "obsolete"
    )
    done = await run_segment(store, runner, "after-pause")
    assert done["battles"]["b"]["round"] == 1
    assert len([e for e in all_events(store) if e["type"] == "BattleNarrated"]) == 1
