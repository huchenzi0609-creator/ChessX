"""CoT-specific retries stay separate from format attempts and race reservations."""

import json
from copy import deepcopy

import pytest
from chessx.llm import FixtureAdapter, ModelError, ModelResult
from test_dialogue_race import setup


def cot_error():
    return ModelError(
        "COT_BUDGET_EXCEEDED",
        "Synthetic reasoning too long.",
        details={"reason": "REASONING_BUDGET_EXCEEDED", "actual_tokens": 1034, "limit": 1000},
    )


async def test_cot_on_last_regular_attempt_gets_an_independent_retry(tmp_path):
    store, runner = setup(tmp_path, parallelism=1)
    contexts = []

    class Adapter(FixtureAdapter):
        async def generate(self, stage, context, schema, *, max_output_tokens=512):
            contexts.append(deepcopy(context))
            if len(contexts) < 3:
                raise ModelError(
                    "MODEL_OUTPUT_INVALID",
                    "Synthetic truncated reply",
                    details={"reason": "RESPONSE_TRUNCATED"},
                )
            if len(contexts) == 3:
                raise cot_error()
            feedback = context["protocol"]["validation_feedback"]
            assert feedback["code"] == "COT_BUDGET_EXCEEDED"
            assert "250 tokens" in feedback["instruction"]
            return ModelResult(
                {"kind": "say", "speech": "VALID_AFTER_RETRY"}, "brief trace", 80, 8, 10, 1
            )

    runner.adapters["race"] = Adapter()
    await runner.step("race")
    current = store.get("race")
    assert current["lifecycle"] == "RUNNING" and current.get("error") is None
    assert current["conversations"]["c"]["messages"][-1]["speech"] == "VALID_AFTER_RETRY"
    assert len(contexts) == 4 and store.call_count("race") == 4
    assert all(c["protocol"]["latest_message"]["message_id"] == "m0" for c in contexts)
    assert "Synthetic reasoning" not in json.dumps(contexts)
    await runner.close()
    store.close()


async def test_both_speculative_candidates_with_bad_cot_retry_single_owner(tmp_path):
    store, runner = setup(tmp_path)
    calls = []

    class Adapter(FixtureAdapter):
        async def generate(self, stage, context, schema, *, max_output_tokens=512):
            calls.append(deepcopy(context))
            feedback = context.get("protocol", {}).get("validation_feedback", {})
            if feedback.get("code") != "COT_BUDGET_EXCEEDED":
                raise cot_error()
            return ModelResult(
                {"kind": "say", "speech": "RETHOUGHT_REPLY"}, "brief trace", 80, 8, 10, 1
            )

    runner.adapters["race"] = Adapter()
    await runner.step("race")
    current = store.get("race")
    assert current["lifecycle"] == "RUNNING" and current.get("error") is None
    assert [c["actor_id"] for c in calls] == ["b", "c", "b"]
    assert current["conversations"]["c"]["messages"][-1]["speech"] == "RETHOUGHT_REPLY"
    assert store.call_count("race") == 3
    assert current["conversations"]["c"]["messages"][-1]["based_on_message_id"] == "m0"
    await runner.close()
    store.close()


@pytest.mark.parametrize(
    "call_budget,expected_calls,lifecycle", [(2, 2, "PAUSED_BUDGET"), (100, 4, "PAUSED_ERROR")]
)
async def test_permanent_bad_cot_cannot_loop_forever_or_exceed_run_budget(
    tmp_path, call_budget, expected_calls, lifecycle
):
    store, runner = setup(tmp_path, calls=call_budget, parallelism=1)

    class Adapter(FixtureAdapter):
        async def generate(self, *args, **kwargs):
            raise cot_error()

    runner.adapters["race"] = Adapter()
    before = store.get("race")
    await runner.step("race")
    after = store.get("race")
    assert after["lifecycle"] == lifecycle and store.call_count("race") == expected_calls
    assert after["conversations"] == before["conversations"] and after["cursor"] == before["cursor"]
    assert after["private"] == before["private"]
    await runner.close()
    store.close()
