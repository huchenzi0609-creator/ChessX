"""A reflection retry uses the same complete sources and canonical ID contract."""

import json
from copy import deepcopy

import httpx
import pytest
from chessx.config import Settings
from chessx.domain.models import Scenario
from chessx.domain.scenarios import create_world
from chessx.llm import FixtureAdapter, ModelResult, ProviderAdapter
from chessx.llm.feedback import validation_feedback
from chessx.runtime.runner import WorldRunner
from chessx.storage import Store


def world():
    scenario = Scenario.model_validate(
        {
            "scenario_id": "citations",
            "title": "Source contract",
            "board": {"rows": 8, "cols": 8},
            "pieces": [
                {
                    "id": "a",
                    "name": "A",
                    "type": "rook",
                    "color": "青",
                    "position": {"row": 0, "col": 0},
                }
            ],
        }
    )
    state = create_world(scenario, {"a": "Private initial knowledge."}, "source_contract")
    state.update(
        mode="fixture", phase="MEMORY", turn=20, completed_turn=19, cursor={"actor_index": 0}
    )
    state["receipts"]["a"] = [
        {
            "receipt_id": "a:1",
            "source_event_id": "source_contract:1",
            "channel": "ConversationMessage",
            "payload": {
                "turn": 1,
                "actor_id": "b",
                "payload": {"speech": "I heard an uncertain report.", "speaker_id": "b"},
            },
        }
    ]
    return state


def result(ids):
    return {
        "memory": "B reported something; it remains uncertain.",
        "goals_markdown": "",
        "goal_decision": "keep",
        "goal_status": "active",
        "reason_summary": "",
        "source_receipt_ids": ids,
    }


async def test_http_schema_failure_then_canonical_retry_keeps_pinned_evidence(tmp_path):
    state = world()
    store = Store(tmp_path / "retry.sqlite3")
    store.create(state)
    runner = WorldRunner(store, Settings(data_dir=tmp_path))
    bodies = []

    def respond(request):
        body = json.loads(request.content)
        data = json.loads(body["messages"][-1]["content"])
        bodies.append(data)
        ctx = data["context"]
        assert ctx["reflection_sources"][0]["receipt_id"] == "a:1"
        content = result(["source_contract:1"] if len(bodies) == 1 else ["a:1"])
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "finish_reason": "stop",
                        "message": {
                            "content": json.dumps(content),
                            "reasoning_content": "Short exposed reasoning.",
                        },
                    }
                ]
            },
        )

    adapter = ProviderAdapter(
        "https://synthetic.invalid/v1",
        "SYNTHETIC_KEY",
        "model",
        tokenizer_agreement=True,
        transport=httpx.MockTransport(respond),
    )
    adapter._probed = True
    runner.adapters[state["world_id"]] = adapter
    indexed = await runner.step(state["world_id"])
    assert not bodies
    current = await runner.step(state["world_id"])
    assert not current.get("error") and current["cursor"]["actor_index"] == 1
    assert len(bodies) == 2
    assert bodies[0]["context"]["reflection_sources"] == bodies[1]["context"]["reflection_sources"]
    assert bodies[0]["output_schema"] == bodies[1]["output_schema"]
    feedback = bodies[1]["context"]["protocol"]["validation_feedback"]
    assert (
        "allowed_source_receipt_ids" in feedback["instruction"]
        and "source_batch" not in feedback["instruction"]
    )
    saved = json.loads(
        store.db.execute("SELECT result FROM operations WHERE status='succeeded'").fetchone()[0]
    )
    assert saved["content"]["source_receipt_ids"] == ["a:1"]
    assert (
        current["private"]["a"]["long_term_memory"] == indexed["private"]["a"]["long_term_memory"]
    )
    assert current["pieces"] == state["pieces"] and current["turn"] == 20
    await runner.close()
    store.close()


@pytest.mark.parametrize("bad", ["b:1", "a:99", "evidence_guess"])
async def test_business_guard_rejects_bad_references_even_in_unvalidated_adapter(tmp_path, bad):
    state = world()
    store = Store(tmp_path / "guard.sqlite3")
    store.create(state)
    runner = WorldRunner(store, Settings(data_dir=tmp_path))

    class Bad(FixtureAdapter):
        async def generate(self, stage, context, schema, **kwargs):
            return ModelResult(result([bad]), "Fixture reasoning.", 10, 10, 10, 1)

    runner.adapters[state["world_id"]] = Bad()
    indexed = await runner.step(state["world_id"])
    before = deepcopy(indexed["private"]["a"])
    current = await runner.step(state["world_id"])
    assert current["error"]["code"] == "MEMORY_SOURCE_INVALID"
    assert current["private"]["a"] == before
    assert current["cursor"]["actor_index"] == 0
    await runner.close()
    store.close()


def test_source_feedback_is_stage_specific():
    reflection = validation_feedback("MEMORY_SOURCE_INVALID", {}, 1024, stage="memory")
    compactor = validation_feedback("MEMORY_SOURCE_INVALID", {}, 2048, stage="memory_compaction")
    assert "reflection_sources" in reflection["instruction"]
    assert "source_batch" not in reflection["instruction"]
    assert "source_batch" in compactor["instruction"]
