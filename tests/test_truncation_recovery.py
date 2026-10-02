"""Truncated reasoning cannot masquerade as a generic JSON error or valid prose."""

import json

import httpx
import pytest
from chessx.llm import ModelError, ProviderAdapter
from chessx.llm.feedback import validation_feedback
from chessx.runtime import schemas
from chessx.runtime.movement import negotiation_schema


def adapter(message):
    provider = ProviderAdapter(
        "https://provider.invalid/v1",
        "SYNTHETIC_TRUNCATION_KEY",
        "model",
        tokenizer_agreement=True,
        transport=httpx.MockTransport(
            lambda request: httpx.Response(
                200, json={"choices": [{"finish_reason": "length", "message": message}]}
            )
        ),
    )
    provider._probed = True
    return provider


async def test_overlong_partial_reasoning_uses_cot_retries_without_accepting_partial_json():
    provider = adapter({"reasoning_content": "reasoning " * 1500, "content": ""})
    with pytest.raises(ModelError) as error:
        await provider.generate(
            "negotiation", {}, negotiation_schema(["stop"]), max_output_tokens=512
        )
    assert error.value.code == "COT_BUDGET_EXCEEDED"
    assert error.value.details["truncated_part"] == "reasoning"
    assert error.value.details["formal_tokens"] == 0
    assert error.value.details["requested_max_tokens"] == 1512
    assert error.value.details["actual_tokens"] > 1000
    assert "reasoning reasoning" not in json.dumps(error.value.details)


async def test_formal_truncation_records_lengths_and_never_repairs_incomplete_text():
    provider = adapter(
        {
            "reasoning_content": "brief trace",
            "content": '{"kind":"say","speech":"INCOMPLETE_SENTINEL',
        }
    )
    with pytest.raises(ModelError) as error:
        await provider.generate(
            "negotiation", {}, negotiation_schema(["stop"]), max_output_tokens=512
        )
    assert error.value.code == "MODEL_OUTPUT_INVALID"
    assert error.value.details["truncated_part"] == "formal_output"
    assert error.value.details["reasoning_tokens"] > 0 and error.value.details["formal_tokens"] > 0
    assert "INCOMPLETE_SENTINEL" not in json.dumps(error.value.details)
    feedback = validation_feedback(error.value.code, error.value.details, 512, stage="negotiation")
    assert (
        "250 tokens" in feedback["instruction"] and "incomplete response" in feedback["instruction"]
    )


async def test_bard_truncated_reasoning_stays_uncapped_but_requires_complete_output():
    provider = adapter({"reasoning_content": "reasoning " * 1500, "content": ""})
    with pytest.raises(ModelError) as error:
        await provider.generate("bard", {}, schemas.BARD, max_output_tokens=8192)
    assert error.value.code == "MODEL_OUTPUT_INVALID"
    assert error.value.details["reason"] == "RESPONSE_TRUNCATED"
    feedback = validation_feedback(error.value.code, error.value.details, 8192, stage="bard")
    assert (
        "250 tokens" not in feedback["instruction"]
        and "no fixed length limit" in feedback["instruction"]
    )


def test_truncation_diagnostics_never_echo_upstream_body():
    # Existing provider tests also exercise this asynchronously; metadata must
    # never include raw envelope/header fields pasted into an exception.
    error = ModelError(
        "MODEL_OUTPUT_INVALID",
        "safe",
        details={
            "reason": "RESPONSE_TRUNCATED",
            "requested_max_tokens": 1512,
            "reasoning_tokens": 1200,
            "formal_tokens": 0,
            "truncated_part": "reasoning",
            "raw_response": "SYNTHETIC_SECRET",
        },
    )
    assert "SYNTHETIC_SECRET" not in json.dumps(error.details)
    assert error.details["requested_max_tokens"] == 1512


async def test_provider_length_classification_gets_independent_retry_after_format_attempts(
    tmp_path,
):
    from test_dialogue_race import setup

    store, runner = setup(tmp_path, parallelism=1)
    sent = []

    def handle(request):
        body = json.loads(request.content)
        sent.append(json.loads(body["messages"][-1]["content"])["context"])
        count = len(sent)
        if count < 3:
            message = {"reasoning_content": "brief trace", "content": '{"kind":'}
            finish = "length"
        elif count == 3:
            message = {"reasoning_content": "reasoning " * 1500, "content": ""}
            finish = "length"
        else:
            message = {
                "reasoning_content": "brief trace",
                "content": '{"kind":"say","speech":"那我停下。","proposal":"stop"}',
            }
            finish = "stop"
        return httpx.Response(
            200, json={"choices": [{"message": message, "finish_reason": finish}]}
        )

    provider = ProviderAdapter(
        "https://provider.invalid/v1",
        "SYNTHETIC_TRUNCATION_KEY",
        "model",
        tokenizer_agreement=True,
        transport=httpx.MockTransport(handle),
    )
    provider._probed = True
    runner.adapters["race"] = provider
    state = store.get("race")
    answer = await runner.ask(
        state, "b", "negotiation", "fix-negotiation", negotiation_schema(["stop"]), {}
    )
    assert answer["proposal"] == "stop" and len(sent) == 4
    assert sent[3]["protocol"]["validation_feedback"]["code"] == "COT_BUDGET_EXCEEDED"
    assert "250 tokens" in sent[3]["protocol"]["validation_feedback"]["instruction"]
    assert [
        r[0] for r in store.db.execute("SELECT error_code FROM operations ORDER BY rowid LIMIT 3")
    ] == ["MODEL_OUTPUT_INVALID", "MODEL_OUTPUT_INVALID", "COT_BUDGET_EXCEEDED"]
    await runner.close()
    store.close()
