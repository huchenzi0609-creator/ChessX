"""Receipt maintenance is offline; subjective reflection remains a model operation."""

import asyncio
import json
import sqlite3
from copy import deepcopy

import pytest
from chessx.config import Settings
from chessx.contexts import build_context
from chessx.domain.models import Scenario
from chessx.domain.scenarios import create_world
from chessx.llm import FixtureAdapter, ModelError
from chessx.runtime.runner import WorldRunner
from chessx.storage import Store


def world(*, count=0, turn=65, phase="PRIMARY"):
    scenario = Scenario.model_validate(
        {
            "scenario_id": "extractive",
            "title": "Offline evidence maintenance",
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
    state = create_world(
        scenario,
        {"a": "# A\n## 目标\n找到可信证据。", "b": "OTHER_OWNER_PRIVATE_DESIGN"},
        "extractive_world",
    )
    state.update(
        mode="fixture",
        phase=phase,
        turn=turn,
        completed_turn=turn - 1,
        cursor={"actor_index": 0, **({"work": None} if phase == "PRIMARY" else {})},
    )
    state["receipts"]["a"] = [receipt(index) for index in range(1, count + 1)]
    own = state["private"]["a"]
    own.update(memory="My earlier subjective assessment.", reflection_watermark=0)
    return state


def receipt(index, speech=None, *, owner="a"):
    return {
        "receipt_id": f"{owner}:{index}",
        "source_event_id": f"extractive_world:{index}",
        "channel": "ConversationMessage",
        "payload": {
            "turn": index,
            "actor_id": "b",
            "payload": {
                "speaker_id": "b",
                "speaker_name": "B",
                "speech": speech or f"这是第{index}则本人已收到的说法，尚未核实。",
            },
        },
    }


class Forbidden(FixtureAdapter):
    async def generate(self, *args, **kwargs):
        raise AssertionError("Archive maintenance must not generate model output")

    async def probe(self):
        raise AssertionError("Archive maintenance must not probe model capabilities")


class Recording(FixtureAdapter):
    def __init__(self):
        super().__init__()
        self.requests = []

    async def generate(self, stage, context, schema, **kwargs):
        self.requests.append((stage, deepcopy(context)))
        return await super().generate(stage, context, schema, **kwargs)


def physical_state(state):
    return {
        key: deepcopy(state[key])
        for key in (
            "turn",
            "completed_turn",
            "pieces",
            "objects",
            "tombstones",
            "conversations",
            "battles",
            "transfer_offers",
        )
    }


async def test_old_archive_is_indexed_in_bounded_steps_with_zero_model_activity(tmp_path):
    state = world(count=500)
    state["private"]["a"]["compaction_watermark"] = 500  # older LLM coverage is independent
    store = Store(tmp_path / "archive.sqlite3")
    store.create(state)
    runner = WorldRunner(store, Settings(data_dir=tmp_path))
    runner.adapters[state["world_id"]] = Forbidden()
    previous = 0
    for _ in range(4):
        current = await runner.step(state["world_id"])
        watermark = current["private"]["a"]["compaction_watermark"]
        assert 0 < watermark - previous <= 128
        assert current["cursor"] == state["cursor"]
        assert physical_state(current) == physical_state(state)
        assert current["usage"] == state["usage"]
        previous = watermark
    own = current["private"]["a"]
    assert own["compaction_watermark"] == 488  # preserve the latest 12 raw receipts
    assert own["extractive_watermark"] == 488
    sources = [e["source_receipt_ids"] for e in own["long_term_memory"].values()]
    assert len(sources) == 488
    assert {tuple(ids) for ids in sources} == {(f"a:{i}",) for i in range(1, 489)}
    assert current["receipts"]["a"] == state["receipts"]["a"]
    assert own["memory"] == state["private"]["a"]["memory"]
    assert own["memory_version"] == state["private"]["a"]["memory_version"]
    assert own["goals"] == state["private"]["a"]["goals"]
    assert own["reflection_watermark"] == 0
    assert runner._operation is None
    assert store.db.execute("SELECT COUNT(*) FROM operations").fetchone()[0] == 0
    await runner.close()
    store.close()


async def test_one_remaining_receipt_needs_no_cot_or_reflection(tmp_path):
    state = world(count=6)
    state["private"]["a"]["compaction_watermark"] = 5
    state["private"]["a"]["extractive_watermark"] = 5
    before = deepcopy(state)
    store = Store(tmp_path / "single.sqlite3")
    store.create(state)
    runner = WorldRunner(store, Settings(data_dir=tmp_path))
    runner.adapters[state["world_id"]] = Forbidden()
    events = await runner.compact_owner(state, "a", 6, reflect_goals=True)
    current = store.commit(state, events, expected_seq=0, expected_generation=0)
    own = current["private"]["a"]
    assert own["compaction_watermark"] == 6
    assert own["extractive_watermark"] == 6
    assert [e["source_receipt_ids"] for e in own["long_term_memory"].values()] == [["a:6"]]
    for field in ("persona", "initial_memory", "memory", "memory_version", "goals", "goal_version"):
        assert own[field] == before["private"]["a"][field]
    assert current["usage"] == before["usage"] and runner._operation is None
    assert physical_state(current) == physical_state(before)
    await runner.close()
    store.close()


async def test_failed_snapshot_transaction_does_not_persist_index_or_watermark(tmp_path):
    state = world(count=40)
    store = Store(tmp_path / "atomic.sqlite3")
    store.create(state)
    store.db.execute(
        """CREATE TRIGGER reject_maintenance BEFORE INSERT ON events
           WHEN json_extract(NEW.event, '$.type')='MemoryCompactionAdvanced'
           BEGIN SELECT RAISE(ABORT, 'synthetic commit failure'); END"""
    )
    runner = WorldRunner(store, Settings(data_dir=tmp_path))
    runner.adapters[state["world_id"]] = Forbidden()
    with pytest.raises(sqlite3.IntegrityError, match="synthetic commit failure"):
        await runner.step(state["world_id"])
    assert store.get(state["world_id"]) == state
    assert store.db.execute("SELECT COUNT(*) FROM events").fetchone()[0] == 0
    await runner.close()
    store.close()


async def test_step_event_resumes_old_cot_pause_without_actor_action_or_api_call(tmp_path):
    state = world(count=40)
    state.update(lifecycle="PAUSED_ERROR", error={"code": "COT_BUDGET_EXCEEDED"})
    store = Store(tmp_path / "resume.sqlite3")
    store.create(state)
    runner = WorldRunner(store, Settings(data_dir=tmp_path))
    runner.adapters[state["world_id"]] = Forbidden()
    await runner.start(state["world_id"], {"command_id": "retry-maintenance", "kind": "step_event"})
    await asyncio.wait_for(runner.tasks[state["world_id"]], 2)
    current = store.get(state["world_id"])
    assert not current.get("error")
    assert current["lifecycle"] == "PAUSED"
    assert current["cursor"] == state["cursor"]
    assert physical_state(current) == physical_state(state)
    assert current["private"]["a"]["compaction_watermark"] == 28
    assert current["usage"] == state["usage"]
    assert store.db.execute("SELECT COUNT(*) FROM operations").fetchone()[0] == 0
    await runner.close()
    store.close()


@pytest.mark.parametrize("turn,expected_calls", [(64, 0), (65, 1)])
async def test_subjective_reflection_is_periodic_and_does_not_replace_evidence(
    tmp_path, turn, expected_calls
):
    state = world(count=2, turn=turn, phase="MEMORY")
    store = Store(tmp_path / f"reflection-{turn}.sqlite3")
    store.create(state)
    runner = WorldRunner(store, Settings(data_dir=tmp_path))
    adapter = Recording()
    runner.adapters[state["world_id"]] = adapter
    indexed = await runner.step(state["world_id"])
    assert not adapter.requests and indexed["cursor"]["actor_index"] == 0
    ledger = deepcopy(indexed["private"]["a"]["long_term_memory"])
    current = await runner.step(state["world_id"])
    assert current["cursor"]["actor_index"] == 1
    assert len(adapter.requests) == expected_calls
    if expected_calls:
        assert adapter.requests[0][0] == "memory"
        assert adapter.requests[0][1]["memory"] == state["private"]["a"]["memory"]
        assert current["private"]["a"]["last_reflection_turn"] == turn
        assert current["private"]["a"]["reflection_watermark"] == 2
    assert current["private"]["a"]["compaction_watermark"] == 2
    assert current["private"]["a"]["extractive_watermark"] == 2
    assert current["private"]["a"]["long_term_memory"] == ledger
    assert current["private"]["a"]["goals"] == state["private"]["a"]["goals"]
    # Re-entering the same world turn must not repeat paid subjective reflection.
    current["cursor"]["actor_index"] = 0
    store.commit(
        current, [], expected_seq=current["seq"], expected_generation=current["generation"]
    )
    repeated = await runner.step(state["world_id"])
    assert repeated["cursor"]["actor_index"] == 1
    assert len(adapter.requests) == expected_calls
    await runner.close()
    store.close()


async def test_failed_reflection_keeps_successfully_committed_evidence_and_old_goals(tmp_path):
    state = world(count=2, phase="MEMORY")
    store = Store(tmp_path / "failed-reflection.sqlite3")
    store.create(state)
    runner = WorldRunner(store, Settings(data_dir=tmp_path))

    class FailedReflection(Recording):
        async def generate(self, stage, context, schema, **kwargs):
            self.requests.append((stage, deepcopy(context)))
            raise ModelError("MODEL_TIMEOUT", "Synthetic offline failure")

    adapter = FailedReflection()
    runner.adapters[state["world_id"]] = adapter
    indexed = await runner.step(state["world_id"])
    current = await runner.step(state["world_id"])
    own = current["private"]["a"]
    assert current["error"]["code"] == "MODEL_TIMEOUT"
    assert current["lifecycle"] == "PAUSED_ERROR"
    assert own["long_term_memory"] == indexed["private"]["a"]["long_term_memory"]
    assert own["compaction_watermark"] == 2 and own["reflection_watermark"] == 0
    assert own["extractive_watermark"] == 2
    assert own["memory"] == state["private"]["a"]["memory"]
    assert own["goals"] == state["private"]["a"]["goals"]
    assert "last_reflection_turn" not in own
    assert current["cursor"]["actor_index"] == 0
    assert len(adapter.requests) == 1 and adapter.requests[0][0] == "memory"
    await runner.close()
    store.close()


async def test_new_critical_receipt_can_trigger_reflection_between_periodic_turns(tmp_path):
    state = world(count=1, turn=64, phase="MEMORY")
    state["receipts"]["a"][0]["channel"] = "ConversationClosed"
    store = Store(tmp_path / "critical.sqlite3")
    store.create(state)
    runner = WorldRunner(store, Settings(data_dir=tmp_path))
    adapter = Recording()
    runner.adapters[state["world_id"]] = adapter
    indexed = await runner.step(state["world_id"])
    assert not adapter.requests
    current = await runner.step(state["world_id"])
    assert len(adapter.requests) == 1 and adapter.requests[0][0] == "memory"
    assert current["private"]["a"]["last_reflection_turn"] == 64
    assert (
        current["private"]["a"]["long_term_memory"] == indexed["private"]["a"]["long_term_memory"]
    )
    await runner.close()
    store.close()


async def test_model_reflection_can_refine_goals_without_replacing_archive_index(tmp_path):
    state = world(count=1, phase="MEMORY")
    store = Store(tmp_path / "refined-goals.sqlite3")
    store.create(state)
    runner = WorldRunner(store, Settings(data_dir=tmp_path))

    class RefineGoals(Recording):
        async def generate(self, stage, context, schema, **kwargs):
            assert stage == "memory"
            supplied = deepcopy(context)
            supplied["protocol"]["fixture_response"] = {
                "memory": "我收到了B的说法，但还需要核实。",
                "goals_markdown": "## 目标\n先核实B的消息，再寻找证据。",
                "goal_decision": "refine",
                "goal_status": "active",
                "reason_summary": "把调查目标具体化为先核实消息。",
                "source_receipt_ids": ["a:1"],
            }
            return await super().generate(stage, supplied, schema, **kwargs)

    adapter = RefineGoals()
    runner.adapters[state["world_id"]] = adapter
    indexed = await runner.step(state["world_id"])
    current = await runner.step(state["world_id"])
    own = current["private"]["a"]
    assert len(adapter.requests) == 1 and own["goal_version"] == 1
    assert own["goals"] == "## 目标\n先核实B的消息，再寻找证据。"
    assert own["memory"] == "我收到了B的说法，但还需要核实。"
    assert own["memory_version"] == 1
    assert own["rolling_summary"] == indexed["private"]["a"]["rolling_summary"]
    assert own["long_term_memory"] == indexed["private"]["a"]["long_term_memory"]
    assert own["extractive_watermark"] == own["reflection_watermark"] == 1
    assert own["persona"] == state["private"]["a"]["persona"]
    assert physical_state(current) == physical_state(state)
    await runner.close()
    store.close()


async def test_index_preserves_same_words_from_different_sources_and_private_anchor(tmp_path):
    state = world(count=2)
    state["receipts"]["a"] = [receipt(1, "我听说他去世了。"), receipt(2, "我听说他去世了。")]
    state["receipts"]["b"] = [receipt(3, "OTHER_OWNER_SECRET_RECEIPT", owner="b")]
    before = deepcopy(state)
    store = Store(tmp_path / "privacy.sqlite3")
    store.create(state)
    runner = WorldRunner(store, Settings(data_dir=tmp_path))
    runner.adapters[state["world_id"]] = Forbidden()
    events = await runner.compact_owner(state, "a", 2)
    current = store.commit(state, events, expected_seq=0, expected_generation=0)
    own = current["private"]["a"]
    entries = list(own["long_term_memory"].values())
    assert len(entries) == 2 and entries[0]["id"] != entries[1]["id"]
    assert {tuple(e["source_receipt_ids"]) for e in entries} == {("a:1",), ("a:2",)}
    assert all(e["owner_id"] == "a" and e["kind"] == "heard_claim" for e in entries)
    context = build_context(current, "a", "ordinary")
    assert context["memory"] == before["private"]["a"]["memory"]
    assert context["evidence_index"]["kind"] == "extractive_receipts_v1"
    assert "我听说他去世了" in "\n".join(context["evidence_index"]["summary_lines"])
    assert "OTHER_OWNER" not in json.dumps(context)
    assert own["persona"] == before["private"]["a"]["persona"]
    assert own["initial_memory"] == before["private"]["a"]["initial_memory"]
    assert not current["private"]["b"]["long_term_memory"]
    await runner.close()
    store.close()
