"""Reproduce memory format/budget failures without real credentials or providers."""

import json
from copy import deepcopy

import httpx
import pytest
from chessx.config import ROOT, Settings
from chessx.domain.scenarios import create_world, load_scenario
from chessx.llm import FixtureAdapter, ModelError, ModelResult, ProviderAdapter
from chessx.llm.provider import compose_messages
from chessx.llm.validation import TokenCounter, validate_output
from chessx.runtime import schemas
from chessx.runtime.runner import WorldRunner
from chessx.storage import Store


def test_memory_prompt_exposes_whole_json_budget_without_mutating_private_design():
    context = {"persona": "PRIVATE_PERSONA", "goals": "PRIVATE_GOALS", "discipline": "DISCIPLINE"}
    original = deepcopy(context)
    messages = compose_messages("memory", context, schemas.MEMORY)
    assert "1024 tokens" in messages[0]["content"]
    assert "350 tokens for memory" in messages[0]["content"]
    assert "source_receipt_ids" in messages[0]["content"]
    assert "PRIVATE_PERSONA" not in messages[0]["content"]
    assert context == original


@pytest.mark.parametrize(
    "case,reason",
    [
        ("budget", "FORMAL_BUDGET_EXCEEDED"),
        ("schema", "SCHEMA_MISMATCH"),
        ("parse", "JSON_INVALID"),
        ("cot", "REASONING_BUDGET_EXCEEDED"),
    ],
)
def test_validation_details_explain_failure_without_private_values(case, reason):
    schema = {
        "type": "object",
        "properties": {"ready": {"const": True}},
        "required": ["ready"],
        "additionalProperties": False,
    }
    content = '{"ready":true}'
    trace = "trace"
    if case == "budget":
        content = json.dumps({"private": "SENTINEL_PRIVATE " * 2000})
    if case == "schema":
        content = '{"SENTINEL_PRIVATE":"SENTINEL_PRIVATE"}'
    if case == "parse":
        content = '{"SENTINEL_PRIVATE":'
    if case == "cot":
        trace = "SENTINEL_PRIVATE " * 2000
    with pytest.raises(ModelError) as error:
        validate_output(content, trace, schema, TokenCounter("cl100k_base"), 1024)
    assert error.value.details["reason"] == reason
    assert "SENTINEL_PRIVATE" not in json.dumps(error.value.details)
    assert "SENTINEL_PRIVATE" not in str(error.value)


def test_error_metadata_whitelist_discards_injected_secrets_and_paths():
    error = ModelError(
        "MODEL_OUTPUT_INVALID",
        "safe",
        details={
            "reason": ["PRIVATE"],
            "raw_response": "SECRET",
            "limit": True,
            "actual_tokens": -1,
            "schema_keywords": ["required", "SECRET"],
            "schema_path": ["SECRET"],
        },
    )
    assert error.details == {"schema_keywords": ["required"]}


async def test_provider_truncation_has_distinct_safe_diagnostic():
    adapter = ProviderAdapter(
        "https://provider.invalid/v1",
        "SYNTHETIC_KEY",
        "model",
        tokenizer_agreement=True,
        transport=httpx.MockTransport(
            lambda request: httpx.Response(
                200,
                json={
                    "choices": [
                        {
                            "finish_reason": "length",
                            "message": {"content": '{"ready":true}', "reasoning_content": "trace"},
                        }
                    ]
                },
            )
        ),
    )
    with pytest.raises(ModelError) as error:
        await adapter.probe()
    assert error.value.details["reason"] == "RESPONSE_TRUNCATED"


@pytest.mark.parametrize("recover", [True, False])
async def test_memory_retry_delivers_actionable_feedback_and_never_advances_on_failure(
    tmp_path, recover
):
    scenario, personas = load_scenario(ROOT / "scenarios/the-seal.json")
    state = create_world(scenario, personas, "memory_recovery")
    state.update(
        mode="fixture",
        phase="MEMORY",
        turn=4,
        completed_turn=3,
        cursor={"actor_index": 0},
        error=None,
    )
    pid = state["actor_order"][0]
    state["receipts"][pid].append(
        {"receipt_id": "r_close", "channel": "ConversationClosed", "payload": {}}
    )
    store = Store(tmp_path / "recovery.sqlite3")
    store.create(state)
    runner = WorldRunner(store, Settings(data_dir=tmp_path))
    contexts = []

    class RecoveringAdapter(FixtureAdapter):
        async def generate(self, stage, context, schema, *, max_output_tokens=512):
            contexts.append(deepcopy(context))
            if len(contexts) == 1 or not recover:
                raise ModelError(
                    "MODEL_OUTPUT_INVALID",
                    "safe",
                    details={
                        "reason": "FORMAL_BUDGET_EXCEEDED",
                        "actual_tokens": 1251,
                        "limit": 1024,
                    },
                )
            content = {
                "memory": "My own concise recollection.",
                "goals_markdown": "",
                "goal_decision": "keep",
                "goal_status": "active",
                "reason_summary": "",
                "source_receipt_ids": ["r_close"],
            }
            validate_output(
                json.dumps(content), "brief trace", schema, self.counter, max_output_tokens
            )
            return ModelResult(content, "brief trace", 100, 60, 10, 1)

    runner.adapters[state["world_id"]] = RecoveringAdapter()
    old_goals = state["private"][pid]["goals"]
    old_persona = state["private"][pid]["persona"]
    await runner.start(state["world_id"], {"command_id": "recover", "kind": "step_event"})
    await runner.tasks[state["world_id"]]
    current = store.get(state["world_id"])
    feedback = contexts[1]["protocol"]["validation_feedback"]
    assert feedback["details"] == {
        "reason": "FORMAL_BUDGET_EXCEEDED",
        "actual_tokens": 1251,
        "limit": 1024,
    }
    assert feedback["formal_output_token_limit"] == 1024
    assert current["private"][pid]["goals"] == old_goals
    assert current["private"][pid]["persona"] == old_persona
    assert current["cursor"]["actor_index"] == (1 if recover else 0)
    assert current["private"][pid]["memory_version"] == state["private"][pid]["memory_version"] + (
        1 if recover else 0
    )
    assert current["private"][pid].get("memory_watermark", 0) == (
        len(state["receipts"][pid]) if recover else 0
    )
    if not recover:
        assert current["lifecycle"] == "PAUSED_ERROR"
        assert current["error"]["details"]["reason"] == "FORMAL_BUDGET_EXCEEDED"
    saved = json.loads(
        store.db.execute("SELECT result FROM operations ORDER BY rowid LIMIT 1").fetchone()[0]
    )
    assert saved["validation"]["limit"] == 1024
    assert len(contexts) == (2 if recover else 3)
    assert sum(e["type"] == "MemoryUpdated" for e in store.events(state["world_id"])["events"]) == (
        1 if recover else 0
    )
    await runner.close()
    store.close()
