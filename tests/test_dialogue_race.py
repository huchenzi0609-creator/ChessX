"""Deterministic gates prove cancellation, fresh replies, budgets and one writer."""

import asyncio
import json
from copy import deepcopy

import pytest
from chessx.config import Settings
from chessx.domain.models import Scenario
from chessx.domain.profile import frozen_rule_manifest, profile_manifest_hash
from chessx.domain.scenarios import create_world
from chessx.llm import FixtureAdapter, ModelError, ModelResult
from chessx.runtime.runner import WorldRunner
from chessx.storage import Store


def setup(tmp_path, *, calls=100, tokens=100000, parallelism=2, legacy=True):
    scenario = Scenario.model_validate(
        {
            "scenario_id": "race",
            "title": "Race fixture",
            "board": {"rows": 8, "cols": 8},
            "pieces": [
                {
                    "id": p,
                    "name": p.upper(),
                    "type": "rook",
                    "color": "青",
                    "position": {"row": 2, "col": i + 1},
                }
                for i, p in enumerate("abc")
            ],
        }
    )
    state = create_world(scenario, {p: f"OWNER_ONLY_{p}" for p in "abc"}, "race")
    # These regression cases freeze the original one-per-turn race protocol.
    if legacy:
        state.update(
            rule_version="2.1",
            rule_profile="demo-v2.1",
            rule_manifest=frozen_rule_manifest("demo-v2.1"),
            rule_manifest_hash=profile_manifest_hash("demo-v2.1"),
        )
    state.update(
        mode="fixture",
        lifecycle="RUNNING",
        phase="ORDINARY",
        turn=1,
        target_turn=1,
        error=None,
        budget={"max_calls": calls, "max_total_tokens": tokens, "max_wall_seconds": 30},
        budget_base_calls=0,
        budget_base_tokens=0,
    )
    initial = {"message_id": "m0", "speaker_id": "a", "speech": "INITIAL_A", "conversation_id": "c"}
    state["conversations"]["c"] = {
        "id": "c",
        "kind": "ordinary",
        "status": "active",
        "members": list("abc"),
        "participant_ids": list("abc"),
        "created_turn": 1,
        "counted_message_ids": ["m0"],
        "messages": [initial],
        "last_spoke_turn": {"a": 1},
        "last_activity_turn": 1,
        "pending_proposal": None,
        "governance_turn": -1,
    }
    for piece in state["pieces"].values():
        piece["conversation_id"] = "c"
    state["cursor"] = {
        "conversation_queue": ["c"],
        "conversation_index": 0,
        "work": {
            "conversation_id": "c",
            "stage": "speak",
            "member_order": list("abc"),
            "member_index": 1,
        },
    }
    if not legacy:
        state["dialogue_permissions"] = {
            "turn": 1,
            "actors": {"a": {"speeches": 1, "permissions": 1}},
        }
        state["cursor"]["work"]["member_order"] = list("bca")
        state["cursor"]["work"]["member_index"] = 0
    store = Store(tmp_path / "race.sqlite3")
    store.create(state)
    store.commit(
        state,
        [
            {
                "type": "ConversationMessage",
                "actor_id": "a",
                "payload": initial,
                "audience": list("abc"),
            }
        ],
        expected_seq=0,
        expected_generation=0,
    )
    runner = WorldRunner(store, Settings(data_dir=tmp_path, dialogue_parallelism=parallelism))
    return store, runner


def result(speech, trace="FIXTURE_PRIVATE_TRACE"):
    return ModelResult({"kind": "say", "speech": speech}, trace, 80, 8, 10, 1)


async def test_first_reply_cancels_peer_and_peer_restarts_from_new_utterance(tmp_path):
    store, runner = setup(tmp_path)
    entered, cancelled = asyncio.Event(), asyncio.Event()
    captures = []

    class Adapter(FixtureAdapter):
        async def generate(self, stage, context, schema, *, max_output_tokens=512):
            captures.append(deepcopy(context))
            pid = context["actor_id"]
            if pid == "b":
                await entered.wait()
                return result("B_REPLY")
            if sum(c["actor_id"] == "c" for c in captures) == 1:
                entered.set()
                try:
                    await asyncio.Event().wait()
                except asyncio.CancelledError:
                    cancelled.set()
                    raise
            assert context["protocol"]["latest_message"]["speech"] == "B_REPLY"
            assert context["dialogue_recall"]["latest_heard"]["speech"] == "B_REPLY"
            return result("C_AFTER_B")

    runner.adapters["race"] = Adapter()
    await asyncio.wait_for(runner.step("race"), 2)
    assert cancelled.is_set()
    await asyncio.wait_for(runner.step("race"), 2)
    final = store.get("race")
    conv = final["conversations"]["c"]
    assert [m["speech"] for m in conv["messages"]] == ["INITIAL_A", "B_REPLY", "C_AFTER_B"]
    assert conv["messages"][-1]["based_on_message_id"] == conv["messages"][-2]["message_id"]
    assert conv["messages"][-1]["conversation_version"] == 2
    assert store.call_count("race") == 3
    discarded = store.db.execute(
        "SELECT status,error_code FROM operations WHERE slot LIKE '%:c' AND attempt=1"
    ).fetchone()
    assert tuple(discarded) == ("obsolete", "DIALOGUE_SUPERSEDED")
    for context in captures:
        other = "OWNER_ONLY_c" if context["actor_id"] == "b" else "OWNER_ONLY_b"
        assert other not in json.dumps(context)
    await runner.close()
    store.close()


async def test_same_tick_completions_commit_only_winner_and_account_discarded_result(tmp_path):
    store, runner = setup(tmp_path)

    class Adapter(FixtureAdapter):
        async def generate(self, stage, context, schema, *, max_output_tokens=512):
            return result("B_WIN" if context["actor_id"] == "b" else "C_DISCARDED")

    runner.adapters["race"] = Adapter()
    await runner.step("race")
    final = store.get("race")
    assert [m["speech"] for m in final["conversations"]["c"]["messages"]] == ["INITIAL_A", "B_WIN"]
    assert final["usage"]["input_tokens"] == 160
    assert "C_DISCARDED" not in json.dumps(store.events("race")["events"])
    assert "C_DISCARDED" not in json.dumps(final["receipts"])
    row = store.db.execute("SELECT result,status FROM operations WHERE slot LIKE '%:c'").fetchone()
    assert row["status"] == "obsolete" and json.loads(row["result"])["discarded"]
    winner_row = store.db.execute(
        "SELECT result,status FROM operations WHERE slot LIKE '%:b'"
    ).fetchone()
    assert (
        winner_row["status"] == "succeeded"
        and json.loads(winner_row["result"])["content"]["speech"] == "B_WIN"
    )
    await runner.close()
    store.close()


async def test_pause_cancels_all_candidates_and_preserves_dialogue_and_cursor(tmp_path):
    store, runner = setup(tmp_path, legacy=False)
    all_started = asyncio.Event()
    started = set()
    cancelled = set()

    class Adapter(FixtureAdapter):
        async def generate(self, stage, context, schema, *, max_output_tokens=512):
            pid = context["actor_id"]
            started.add(pid)
            if len(started) == 2:
                all_started.set()
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                cancelled.add(pid)
                raise

    runner.adapters["race"] = Adapter()
    await runner.start("race", {"command_id": "pause-race", "kind": "run", "turns": 1})
    await asyncio.wait_for(all_started.wait(), 2)
    before = store.get("race")
    runner.pause("race")
    with pytest.raises(asyncio.CancelledError):
        await runner.tasks["race"]
    final = store.get("race")
    assert cancelled == {"b", "c"}
    assert final["lifecycle"] == "PAUSED" and final["generation"] == before["generation"] + 1
    assert final["cursor"] == before["cursor"] and final["conversations"] == before["conversations"]
    assert all(r[0] == "obsolete" for r in store.db.execute("SELECT status FROM operations"))
    await runner.close()
    store.close()


@pytest.mark.parametrize("parallelism,call_budget", [(1, 100), (2, 1)])
async def test_one_call_remaining_or_serial_setting_does_not_start_extra_request(
    tmp_path, parallelism, call_budget
):
    store, runner = setup(tmp_path, calls=call_budget, parallelism=parallelism)

    class Adapter(FixtureAdapter):
        async def generate(self, stage, context, schema, *, max_output_tokens=512):
            return result("ONLY_B")

    runner.adapters["race"] = Adapter()
    await runner.step("race")
    assert store.call_count("race") == 1
    assert store.get("race")["conversations"]["c"]["messages"][-1]["speaker_id"] == "b"
    await runner.close()
    store.close()


async def test_invalid_fast_response_cannot_publish_before_valid_peer(tmp_path):
    store, runner = setup(tmp_path)

    class Adapter(FixtureAdapter):
        async def generate(self, stage, context, schema, *, max_output_tokens=512):
            if context["actor_id"] == "b":
                raise ModelError("MODEL_OUTPUT_INVALID", "Synthetic invalid response")
            return result("VALID_C")

    runner.adapters["race"] = Adapter()
    await runner.step("race")
    conv = store.get("race")["conversations"]["c"]
    assert conv["messages"][-1]["speech"] == "VALID_C"
    assert "b" not in conv["last_spoke_turn"] and conv["last_spoke_turn"]["c"] == 1
    assert store.call_count("race") == 2
    await runner.close()
    store.close()


async def test_fiftieth_message_closes_conversation_and_never_publishes_loser(tmp_path):
    store, runner = setup(tmp_path)
    current = store.get("race")
    current["conversations"]["c"]["counted_message_ids"] = [f"m{i}" for i in range(49)]
    store.commit(
        current, [], expected_seq=current["seq"], expected_generation=current["generation"]
    )

    class Adapter(FixtureAdapter):
        async def generate(self, stage, context, schema, *, max_output_tokens=512):
            return result("WINNER" if context["actor_id"] == "b" else "DISCARDED")

    runner.adapters["race"] = Adapter()
    await runner.step("race")
    conv = store.get("race")["conversations"]["c"]
    assert conv["status"] == "closed" and len(conv["counted_message_ids"]) == 50
    assert conv["participant_ids"] == list("abc") and conv["closed_turn"] == 1
    assert "DISCARDED" not in json.dumps(store.events("race")["events"])
    await runner.close()
    store.close()


async def test_joint_token_reservation_falls_back_to_one_request(tmp_path):
    from chessx.contexts.budgets import request_input_tokens
    from chessx.runtime.dialogue import ORDINARY_SCHEMA

    store, runner = setup(tmp_path)
    adapter = FixtureAdapter()
    runner.adapters["race"] = adapter
    current = store.get("race")
    protocol = {"conversation_id": "c", "conversation_version": 1}
    context = runner.ordinary_candidate_context(current, "c", "b", ORDINARY_SCHEMA, protocol)
    single = request_input_tokens("ordinary", context, ORDINARY_SCHEMA, adapter.counter, 512) + 1512
    current["budget"]["max_total_tokens"] = single + 5
    pid, answer = await runner.ask_ordinary_race(
        current, "c", ["b", "c"], ORDINARY_SCHEMA, protocol, lambda pid, answer: None
    )
    assert pid == "b" and answer["kind"] in {"say", "pass"}
    assert store.call_count("race") == 1
    await runner.close()
    store.close()


async def test_latest_ten_speech_schedule_cancels_and_rethinks_each_new_version(tmp_path):
    store, runner = setup(tmp_path, legacy=False)
    peer_entered, peer_cancelled = asyncio.Event(), asyncio.Event()
    seen = []

    class Adapter(FixtureAdapter):
        async def generate(self, stage, context, schema, *, max_output_tokens=512):
            pid = context["actor_id"]
            seen.append((pid, deepcopy(context["protocol"])))
            if pid == "b":
                await peer_entered.wait()
                return result("B_LATEST")
            if pid == "c" and sum(owner == "c" for owner, _ in seen) == 1:
                peer_entered.set()
                try:
                    await asyncio.Event().wait()
                except asyncio.CancelledError:
                    peer_cancelled.set()
                    raise
            if pid == "c":
                assert context["protocol"]["latest_message"]["speech"] == "B_LATEST"
                return result("C_RECONSIDERED")
            await asyncio.Event().wait()

    runner.adapters["race"] = Adapter()
    await asyncio.wait_for(runner.step("race"), 2)
    assert peer_cancelled.is_set()
    await asyncio.wait_for(runner.step("race"), 2)
    state = store.get("race")
    messages = state["conversations"]["c"]["messages"]
    assert [message["speech"] for message in messages] == [
        "INITIAL_A",
        "B_LATEST",
        "C_RECONSIDERED",
    ]
    assert messages[-1]["based_on_message_id"] == messages[-2]["message_id"]
    usage = state["dialogue_permissions"]["actors"]
    assert usage["a"]["speeches"] == usage["b"]["speeches"] == usage["c"]["speeches"] == 1
    assert seen[0][1]["remaining_speeches_this_world_turn"] == 10
    store.close()
