"""Regression coverage for lost-prefix compaction and owner-only durable recall."""

import asyncio
import json
from copy import deepcopy

import pytest
from chessx.config import Settings
from chessx.contexts import build_context
from chessx.contexts.memory import recall
from chessx.domain.models import Scenario
from chessx.domain.scenarios import create_world
from chessx.llm import FixtureAdapter, ModelError, ModelResult
from chessx.runtime.runner import WorldRunner
from chessx.storage import Store


def world():
    scenario = Scenario.model_validate(
        {
            "scenario_id": "rolling",
            "title": "Memory fixture",
            "board": {"rows": 8, "cols": 8},
            "pieces": [
                {
                    "id": "a",
                    "name": "A",
                    "type": "rook",
                    "color": "青",
                    "position": {"row": 0, "col": 0},
                },
                {
                    "id": "b",
                    "name": "B",
                    "type": "rook",
                    "color": "青",
                    "position": {"row": 7, "col": 7},
                },
            ],
        }
    )
    state = create_world(scenario, {"a": "A's design", "b": "OTHER_SECRET"}, "rolling_world")
    state.update(mode="fixture", phase="PRIMARY", turn=51, cursor={"actor_index": 0, "work": None})
    return state


def receipt(index, speech=None):
    return {
        "receipt_id": f"a:{index}",
        "source_event_id": f"rolling_world:{index}",
        "channel": "ConversationMessage",
        "payload": {
            "turn": index,
            "actor_id": "b",
            "payload": {
                "speaker_id": "b",
                "speech": speech or f"Message {index}: a different observation.",
            },
        },
    }


def result(context, additions=None):
    return ModelResult(
        {
            "summary": "Recent incremental summary.",
            "additions": additions or [],
            "goal_decision": "keep",
            "goal_status": "active",
            "goals_markdown": "",
            "reason_summary": "",
        },
        "brief reasoning",
        50,
        50,
        10,
        1,
    )


async def test_oldest_prefix_is_compacted_before_decision_and_only_covered_prefix_advances(
    tmp_path,
):
    state = world()
    state["receipts"]["a"] = [receipt(i) for i in range(1, 101)]
    state["private"]["a"]["memory_watermark"] = 100  # legacy watermark was unreliable
    store = Store(tmp_path / "covered.sqlite3")
    store.create(state)
    runner = WorldRunner(store, Settings(data_dir=tmp_path))
    requests = []

    class Recording(FixtureAdapter):
        async def generate(self, stage, context, schema, **kwargs):
            requests.append((stage, deepcopy(context)))
            return result(context)

    runner.adapters[state["world_id"]] = Recording()
    current = await runner.step(state["world_id"])
    stage, context = requests[0]
    assert stage == "memory_compaction"
    assert context["source_batch"][0]["receipt_id"] == "a:1"
    end = context["coverage"]["end_receipt_index"]
    assert end <= 32 and current["private"]["a"]["compaction_watermark"] == end
    assert current["cursor"] == state["cursor"]
    assert "OTHER_SECRET" not in json.dumps(context)
    await runner.close()
    store.close()


@pytest.mark.parametrize("failure", ["MODEL_OUTPUT_INVALID", "MODEL_TIMEOUT"])
async def test_failed_compaction_preserves_summary_ledger_watermark_and_cursor(tmp_path, failure):
    state = world()
    state["receipts"]["a"] = [receipt(i) for i in range(40)]
    own = state["private"]["a"]
    own.update(
        rolling_summary="Old anchor",
        memory="Old anchor",
        long_term_memory={"old": {"text": "Old fact"}},
    )
    store = Store(tmp_path / "failure.sqlite3")
    store.create(state)
    runner = WorldRunner(store, Settings(data_dir=tmp_path))

    class Failed(FixtureAdapter):
        async def generate(self, *args, **kwargs):
            raise ModelError(failure, "Synthetic failure")

    runner.adapters[state["world_id"]] = Failed()
    current = await runner.step(state["world_id"])
    for field in (
        "rolling_summary",
        "memory",
        "long_term_memory",
        "compaction_watermark",
        "memory_version",
    ):
        assert current["private"]["a"][field] == own[field]
    assert current["cursor"] == state["cursor"]
    assert current["lifecycle"] == "PAUSED_ERROR"
    await runner.close()
    store.close()


async def test_cancelled_compaction_cannot_commit_after_generation_changes(tmp_path):
    state = world()
    state["receipts"]["a"] = [receipt(i) for i in range(40)]
    store = Store(tmp_path / "cancel.sqlite3")
    store.create(state)
    runner = WorldRunner(store, Settings(data_dir=tmp_path))
    entered, release = asyncio.Event(), asyncio.Event()

    class Slow(FixtureAdapter):
        async def generate(self, *args, **kwargs):
            entered.set()
            await release.wait()
            return result({})

    runner.adapters[state["world_id"]] = Slow()
    task = asyncio.create_task(runner.step(state["world_id"]))
    await asyncio.wait_for(entered.wait(), 2)
    runner.pause(state["world_id"])
    release.set()
    await task
    current = store.get(state["world_id"])
    assert current["private"]["a"]["compaction_watermark"] == 0
    assert current["cursor"] == state["cursor"]
    assert store.db.execute("SELECT status FROM operations").fetchone()[0] == "obsolete"
    await runner.close()
    store.close()


async def test_durable_heard_news_survives_later_summary_omission_and_reload(tmp_path):
    state = world()
    state["receipts"]["a"] = [receipt(1, "The messenger said the old king died.")]
    store = Store(tmp_path / "durable.sqlite3")
    store.create(state)
    runner = WorldRunner(store, Settings(data_dir=tmp_path))

    class Remember(FixtureAdapter):
        async def generate(self, stage, context, schema, **kwargs):
            additions = (
                [
                    {
                        "text": "B reported that the old king died.",
                        "kind": "heard_claim",
                        "salience": "important",
                        "source_receipt_ids": ["a:1"],
                    }
                ]
                if context["coverage"]["start_receipt_index"] == 0
                else []
            )
            return result(context, additions)

    runner.adapters[state["world_id"]] = Remember()
    events = await runner.compact_owner(state, "a", 1)
    state = store.commit(
        state, events, expected_seq=0, expected_generation=0, operation=runner._operation
    )
    state["receipts"]["a"].append(receipt(2, "Later unrelated news."))
    events = await runner.compact_owner(state, "a", len(state["receipts"]["a"]))
    state = store.commit(
        state, events, expected_seq=state["seq"], expected_generation=0, operation=runner._operation
    )
    current = store.get(state["world_id"])
    knowledge = build_context(
        current, "a", "ordinary", {"latest_message": {"speech": "Did the old king die?"}}
    )["memory_recall"]
    assert any(
        "old king died" in e["text"] and e["kind"] == "heard_claim" for e in knowledge["long_term"]
    )
    assert all(e["owner_id"] == "a" for e in knowledge["long_term"])
    assert not recall(current, "b")["long_term"]
    assert "old king died" not in current["private"]["a"]["memory"]
    await runner.close()
    store.close()


@pytest.mark.parametrize("kind,source", [("heard_claim", "b:secret"), ("observed_fact", "a:1")])
async def test_sources_cannot_escape_supplied_batch_or_turn_speech_into_world_truth(
    tmp_path, kind, source
):
    state = world()
    state.update(phase="MEMORY", turn=5, cursor={"actor_index": 0})
    state["receipts"]["a"] = [receipt(1, "A speaker makes an unverified claim.")]
    store = Store(tmp_path / "sources.sqlite3")
    store.create(state)
    runner = WorldRunner(store, Settings(data_dir=tmp_path))

    class Invalid(FixtureAdapter):
        async def generate(self, *args, **kwargs):
            return result(
                {},
                [
                    {
                        "text": "A claim",
                        "kind": kind,
                        "salience": "important",
                        "source_receipt_ids": [source],
                    }
                ],
            )

    runner.adapters[state["world_id"]] = Invalid()
    current = await runner.step(state["world_id"])
    assert current["error"]["code"] == "MEMORY_SOURCE_INVALID"
    assert current["private"]["a"]["compaction_watermark"] == 0
    assert not current["private"]["a"]["long_term_memory"]
    await runner.close()
    store.close()


def test_archive_recovers_old_personally_heard_news_despite_late_noise_and_no_model_call():
    state = world()
    state["receipts"]["a"] = [receipt(1, "波吕波斯去世了。")] + [
        receipt(i, f"随后的路上见闻{i}") for i in range(2, 100)
    ]
    state["receipts"]["b"] = [receipt(101, "OTHER_SECRET")]
    context = build_context(
        state, "a", "negotiation", {"latest_message": {"speech": "波吕波斯的死讯，你听过吗？"}}
    )
    assert any(r["receipt_id"] == "a:1" for r in context["memory_recall"]["archive_evidence"])
    assert "OTHER_SECRET" not in json.dumps(context)
    assert context["navigation"]["coordinate_system"]
    assert not context["navigation"]["can_choose_move_in_this_stage"]


async def test_multiple_batches_cover_every_semantic_source_once_without_clock_advance(tmp_path):
    state = world()
    state.update(phase="MEMORY", cursor={"actor_index": 0})
    state["receipts"]["a"] = [receipt(i) for i in range(1, 121)]
    store = Store(tmp_path / "complete.sqlite3")
    store.create(state)
    runner = WorldRunner(store, Settings(data_dir=tmp_path, memory_batch_size=32))
    covered = []

    class Recording(FixtureAdapter):
        async def generate(self, stage, context, schema, **kwargs):
            covered.extend(r["receipt_id"] for r in context["source_batch"])
            return result(context)

    runner.adapters[state["world_id"]] = Recording()
    for _ in range(10):
        current = await runner.step(state["world_id"])
        assert current["turn"] == 51
        if current["cursor"]["actor_index"] == 1:
            break
    assert current["cursor"]["actor_index"] == 1
    assert covered == [f"a:{i}" for i in range(1, 121)]
    assert current["private"]["a"]["compaction_watermark"] >= 120
    await runner.close()
    store.close()


def test_prompt_provenance_is_bounded_but_complete_durable_sources_are_untouched(tmp_path):
    state = world()
    entry = {
        "id": "fact",
        "owner_id": "a",
        "text": "A consequential report.",
        "kind": "heard_claim",
        "salience": "important",
        "learned_turn": 1,
        "source_receipt_ids": [f"a:{i}" for i in range(1000)],
    }
    state["private"]["a"]["long_term_memory"] = {"fact": entry}
    before = deepcopy(state)
    remembered = recall(state, "a")["long_term"][0]
    assert remembered["source_count"] == 1000 and len(remembered["source_receipt_ids"]) == 4
    store = Store(tmp_path / "bounded.sqlite3")
    store.create(state)
    runner = WorldRunner(store, Settings(data_dir=tmp_path))
    preserved = runner.compaction_input(state, "a", 0, 0)["retained_important_facts"][0]
    assert preserved["source_count"] == 1000 and len(preserved["source_receipt_ids"]) == 4
    assert state == before
    store.close()


def test_compaction_keeps_nonverbal_proposals_votes_and_structural_changes():
    from chessx.contexts.memory import NOISE, compact_receipt

    r = receipt(1)
    r.update(channel="NegotiationPermissionUsed")
    r["payload"]["payload"] = {
        "proposal": "pass",
        "kind": "pass",
        "mode": "passage",
        "member_ids": ["a", "b"],
    }
    assert r["channel"] not in NOISE
    assert compact_receipt(r)["payload"] == r["payload"]["payload"]
    r["channel"] = "ReleaseVoteCast"
    r["payload"]["payload"] = {
        "approve": True,
        "loser_id": "b",
        "battle_id": "battle",
        "private": "NO_LEAK",
    }
    assert compact_receipt(r)["payload"] == {
        "approve": True,
        "loser_id": "b",
        "battle_id": "battle",
    }


@pytest.mark.parametrize("stage", ["invite_response", "surrender", "accept_transfer"])
def test_position_and_personal_recall_are_available_in_all_character_decision_stages(stage):
    state = world()
    context = build_context(state, "a", stage)
    assert "navigation" in context and "memory_recall" in context
    assert not context["navigation"]["can_choose_move_in_this_stage"]


def test_noise_compaction_metadata_is_not_public_or_bard_material():
    from chessx.api.projections import public_event
    from chessx.contexts.builders import bard_context

    state = world()
    event = {
        "type": "MemoryCompactionAdvanced",
        "seq": 1,
        "actor_id": "a",
        "payload": {"covered_end": 400},
    }
    assert public_event(event) is None
    assert not bard_context(state, 1, 1, [event])["events"]


async def test_reasoning_failure_splits_unacknowledged_prefix_and_remembers_successful_batch_size(
    tmp_path,
):
    state = world()
    state["receipts"]["a"] = [receipt(i) for i in range(1, 41)]
    store = Store(tmp_path / "adaptive.sqlite3")
    store.create(state)
    runner = WorldRunner(store, Settings(data_dir=tmp_path, memory_batch_size=32))
    sizes = []

    class SmallOnly(FixtureAdapter):
        async def generate(self, stage, context, schema, **kwargs):
            size = len(context["source_batch"])
            sizes.append(size)
            if size > 4:
                raise ModelError("COT_BUDGET_EXCEEDED", "Synthetic reasoning pressure")
            assert context["source_batch"][0]["receipt_id"] == "a:1"
            assert context["protocol"]["validation_feedback"]["code"] == "COT_BUDGET_EXCEEDED"
            return result(context)

    runner.adapters[state["world_id"]] = SmallOnly()
    current = await runner.step(state["world_id"])
    assert sizes == [28, 14, 7, 3]
    assert current["private"]["a"]["compaction_watermark"] == 3
    assert current["private"]["a"]["compaction_batch_limit"] == 3
    assert current["cursor"] == state["cursor"]
    assert store.call_count(state["world_id"]) == 4
    await runner.close()
    store.close()


def test_legacy_end_of_history_summary_does_not_seed_the_oldest_prefix(tmp_path):
    state = world()
    state["private"]["a"].pop("rolling_summary")
    state["private"]["a"]["memory"] = "Knowledge from much later events."
    state["receipts"]["a"] = [receipt(1)]
    store = Store(tmp_path / "chronology.sqlite3")
    store.create(state)
    runner = WorldRunner(store, Settings(data_dir=tmp_path))
    assert runner.compaction_input(state, "a", 0, 1)["previous_summary"] == ""
    assert state["private"]["a"]["memory"] == "Knowledge from much later events."
    store.close()


async def test_scheduled_final_batch_can_reflect_goals_but_maintenance_cannot(tmp_path):
    state = world()
    state.update(phase="MEMORY", turn=5, cursor={"actor_index": 0})
    state["receipts"]["a"] = [receipt(1)]
    store = Store(tmp_path / "goals.sqlite3")
    store.create(state)
    runner = WorldRunner(store, Settings(data_dir=tmp_path))

    class Reflect(FixtureAdapter):
        async def generate(self, stage, context, schema, **kwargs):
            assert context["reflect_goals"] and "current_goals" in context
            response = result(context)
            return ModelResult(
                {
                    **response.content,
                    "goal_decision": "refine",
                    "goals_markdown": "Revised personal goal.",
                },
                "brief reasoning",
                50,
                50,
                10,
                1,
            )

    runner.adapters[state["world_id"]] = Reflect()
    current = await runner.step(state["world_id"])
    assert current["private"]["a"]["goal_version"] == 1
    assert current["private"]["a"]["goals"] == "Revised personal goal."
    assert any(e["type"] == "GoalUpdated" for e in store.events(state["world_id"])["events"])
    maintenance = runner.compaction_input(current, "a", 0, 1)
    assert maintenance["reflect_goals"] is False and "current_goals" not in maintenance
    await runner.close()
    store.close()


async def test_default_fixture_can_execute_pressure_compaction_without_goal_side_effects(tmp_path):
    state = world()
    state["receipts"]["a"] = [receipt(i) for i in range(1, 41)]
    store = Store(tmp_path / "fixture-pressure.sqlite3")
    store.create(state)
    runner = WorldRunner(store, Settings(data_dir=tmp_path))
    current = await runner.step(state["world_id"])
    assert not current.get("error")
    assert current["private"]["a"]["compaction_watermark"] > 0
    assert current["private"]["a"]["goals"] == state["private"]["a"]["goals"]
    assert current["private"]["a"]["long_term_memory"]
    await runner.close()
    store.close()
