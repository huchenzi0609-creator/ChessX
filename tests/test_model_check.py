"""Diagnostics must never print upstream echoes or silently spend generation calls."""

import importlib.util
import json
from pathlib import Path

import httpx
import pytest
from chessx.config import Settings
from pydantic import SecretStr

spec = importlib.util.spec_from_file_location(
    "check_model", Path(__file__).resolve().parents[1] / "scripts/check_model.py"
)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
KEY = "SYNTHETIC_DIAGNOSTIC_KEY"


def settings():
    return Settings(
        api_key=SecretStr(KEY),
        base_url="https://provider.invalid/v1",
        model="fixture-model",
        tokenizer_agreement=True,
    )


async def test_model_list_only_check_does_not_generate():
    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(200, json={"data": [{"id": "fixture-model"}]})

    result = await module.check(settings(), transport=httpx.MockTransport(handler))
    assert result["passed"]
    assert result["generation_calls"] == 0
    assert not result["generation_checked"]
    assert len(requests) == 1 and requests[0].method == "GET"
    assert KEY not in json.dumps(result)


async def test_explicit_probe_is_one_generation_with_reasoning_and_json_validation():
    requests = []

    def handler(request):
        requests.append(request)
        if request.method == "GET":
            return httpx.Response(200, json={"data": [{"id": "fixture-model"}]})
        assert json.loads(request.content)["max_tokens"] == 384
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "finish_reason": "stop",
                        "message": {
                            "content": '{"ready":true}',
                            "reasoning_content": "Verify readiness.",
                        },
                    }
                ]
            },
        )

    result = await module.check(settings(), probe=True, transport=httpx.MockTransport(handler))
    assert result["passed"] and result["strict_json"] and result["exposed_reasoning"]
    assert result["generation_calls"] == 1
    assert len(requests) == 2
    assert KEY not in json.dumps(result)


@pytest.mark.parametrize("status", [401, 402, 500])
async def test_model_list_errors_are_sanitized_and_do_not_generate(status):
    result = await module.check(
        settings(),
        probe=True,
        transport=httpx.MockTransport(lambda request: httpx.Response(status, text=KEY)),
    )
    assert not result["passed"]
    assert result["generation_calls"] == 0
    assert not result["generation_checked"]
    assert KEY not in json.dumps(result)


async def test_missing_model_does_not_generate():
    result = await module.check(
        settings(),
        probe=True,
        transport=httpx.MockTransport(lambda request: httpx.Response(200, json={"data": []})),
    )
    assert result["error_code"] == "MODEL_NOT_LISTED"
    assert result["generation_calls"] == 0
