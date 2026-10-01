import asyncio
import json

import httpx
import pytest
from chessx.llm import FixtureAdapter, ModelError, ProviderAdapter
from chessx.llm.validation import TokenCounter, extract_response, validate_output

READY = {
    "type": "object",
    "properties": {"ready": {"const": True}},
    "required": ["ready"],
    "additionalProperties": False,
}
WAIT = {
    "type": "object",
    "properties": {"kind": {"const": "wait"}},
    "required": ["kind"],
    "additionalProperties": False,
}


def adapter(message: dict, *, status: int = 200, finish_reason: str = "stop"):
    def handler(request):
        assert request.headers["Authorization"] == "Bearer SECRET_TEST_KEY"
        if status != 200:
            return httpx.Response(status, json={"error": "SECRET_TEST_KEY upstream echo"})
        return httpx.Response(
            200, json={"choices": [{"message": message, "finish_reason": finish_reason}]}
        )

    return ProviderAdapter(
        "https://provider.invalid/v1",
        "SECRET_TEST_KEY",
        "any-model",
        tokenizer_agreement=True,
        transport=httpx.MockTransport(handler),
    )


@pytest.mark.parametrize(
    "message",
    [
        {"content": '{"ready":true}'},
        {"content": '{"ready":true}', "reason_summary": "Summary is not CoT"},
        {"content": '{"ready":true}', "reasoning_tokens": 4},
        {"content": '{"ready":true}', "reasoning": " "},
    ],
)
async def test_probe_rejects_unexposed_reasoning(message):
    with pytest.raises(ModelError) as error:
        await adapter(message).probe()
    assert error.value.code == "MODEL_COT_UNSUPPORTED"


@pytest.mark.parametrize(
    "message",
    [
        {"content": '{"ready":true}', "reasoning_content": "I verify readiness."},
        {"content": '<think>I verify readiness.</think>{"ready":true}'},
    ],
)
async def test_probe_and_strict_separation(message):
    live = adapter(message)
    assert "SECRET_TEST_KEY" not in repr(live)
    assert (await live.probe())["exposed_reasoning"] is True
    result = await live.generate("test", {}, READY)
    assert result.content == {"ready": True}
    assert result.reasoning == "I verify readiness."
    assert result.reasoning_tokens > 0


async def test_model_must_pass_probe():
    with pytest.raises(ModelError, match="capability probing"):
        await adapter({"content": "{}", "reasoning": "trace"}).generate("test", {}, {})


async def test_total_timeout_closes_response_despite_keepalive_bytes():
    closed = asyncio.Event()

    class KeepaliveStream(httpx.AsyncByteStream):
        async def __aiter__(self):
            while True:
                yield b"\n"
                await asyncio.sleep(0.005)

        async def aclose(self):
            closed.set()

    live = ProviderAdapter(
        "https://provider.invalid/v1",
        "SYNTHETIC_TIMEOUT_KEY",
        "any-model",
        timeout=0.05,
        tokenizer_agreement=True,
        transport=httpx.MockTransport(
            lambda request: httpx.Response(200, stream=KeepaliveStream())
        ),
    )
    with pytest.raises(ModelError) as error:
        await asyncio.wait_for(live.probe(), 1)
    assert error.value.code == "MODEL_TIMEOUT"
    assert closed.is_set()
    assert getattr(live, "last_probe_result", None) is None


def test_tokenizer_must_be_verified():
    with pytest.raises(ModelError) as error:
        ProviderAdapter("https://provider.invalid/v1", "key", "model")
    assert error.value.code == "TOKENIZER_UNAVAILABLE"
    with pytest.raises(ModelError):
        TokenCounter("unknown-encoding")


@pytest.mark.parametrize(
    "content",
    [
        '{"ready":true,"ready":true}',
        '{"ready":true} trailing',
        '{"ready":true,"extra":"secret"}',
        '{"ready":true,"reasoning":"secret"}',
        '{"ready":NaN}',
        "[]",
    ],
)
def test_complete_strict_json(content):
    with pytest.raises(ModelError) as error:
        validate_output(content, "trace", READY, TokenCounter("cl100k_base"), 512)
    assert "secret" not in str(error.value)


def test_limits_and_reasoning_contamination():
    counter = TokenCounter("cl100k_base")
    with pytest.raises(ModelError) as error:
        validate_output('{"ready":true}', "trace " * 1100, READY, counter, 512)
    assert error.value.code == "COT_BUDGET_EXCEEDED"
    with pytest.raises(ModelError) as error:
        validate_output(
            json.dumps({"speech": "word " * 101}), "trace", {"type": "object"}, counter, 512
        )
    assert error.value.code == "SPEECH_BUDGET_EXCEEDED"
    with pytest.raises(ModelError):
        extract_response({"content": '<think>trace</think>{"ready":true}<think>extra</think>'})


@pytest.mark.parametrize(
    "status,code",
    [(401, "MODEL_AUTH_FAILED"), (429, "MODEL_RATE_LIMITED"), (500, "MODEL_HTTP_ERROR")],
)
async def test_provider_errors_do_not_echo_upstream(status, code):
    with pytest.raises(ModelError) as error:
        await adapter({}, status=status).probe()
    assert error.value.code == code
    assert "SECRET_TEST_KEY" not in str(error.value)


async def test_truncation_is_not_formal_output():
    with pytest.raises(ModelError) as error:
        await adapter(
            {"content": '{"ready":true}', "reasoning": "trace"}, finish_reason="length"
        ).probe()
    assert error.value.code == "MODEL_OUTPUT_INVALID"


async def test_fixture_is_explicit_and_schema_checked():
    fixture = FixtureAdapter()
    result = await fixture.generate(
        "action", {"observation": {"self": {"rest_remaining": 1}}}, WAIT
    )
    assert result.content == {"kind": "wait"}
    assert "not AI reasoning" in result.reasoning
    assert (await fixture.probe())["engineering_fixture"] is True
    with pytest.raises(ModelError):
        await fixture.generate(
            "action", {"protocol": {"fixture_response": {"kind": "invent_object"}}}, WAIT
        )


async def test_network_error_message_is_sanitized():
    def handler(request):
        raise httpx.ConnectError("SECRET_TEST_KEY raw sensitive provider message", request=request)

    live = ProviderAdapter(
        "https://provider.invalid/v1",
        "SECRET_TEST_KEY",
        "any-model",
        tokenizer_agreement=True,
        transport=httpx.MockTransport(handler),
    )
    with pytest.raises(ModelError) as error:
        await live.probe()
    assert error.value.code == "MODEL_TRANSPORT_ERROR"
    assert "SECRET_TEST_KEY" not in repr(error.value)


async def test_valid_probe_does_not_allow_missing_trace_later():
    responses = iter(
        [
            {"content": '{"ready":true}', "reasoning_content": "ready"},
            {"content": '{"kind":"wait"}'},
        ]
    )

    def handler(request):
        return httpx.Response(
            200, json={"choices": [{"message": next(responses), "finish_reason": "stop"}]}
        )

    live = ProviderAdapter(
        "https://provider.invalid/v1",
        "fixture-auth-only",
        "model",
        tokenizer_agreement=True,
        transport=httpx.MockTransport(handler),
    )
    await live.probe()
    with pytest.raises(ModelError) as error:
        await live.generate("action", {}, WAIT)
    assert error.value.code == "MODEL_COT_MISSING"


@pytest.mark.parametrize(
    "message",
    [
        {"content": '{"ready":true}', "reasoning_content": "SECRET_TEST_KEY"},
        {"content": '{"ready":true,"credential":"SECRET_TEST_KEY"}', "reasoning_content": "trace"},
        {"content": '<think>SECRET_TEST_KEY</think>{"ready":true}'},
    ],
)
async def test_success_response_cannot_echo_credential(message):
    with pytest.raises(ModelError) as error:
        await adapter(message).probe()
    assert error.value.code == "MODEL_SECRET_ECHO"
    assert "SECRET_TEST_KEY" not in str(error.value)


@pytest.mark.parametrize(
    "endpoint",
    [
        "http://provider.invalid/v1",
        "http://192.168.1.20/v1",
        "https://user:password@provider.invalid/v1",
        "https://provider.invalid/v1?key=secret",
    ],
)
def test_remote_transport_requires_https_without_url_credentials(endpoint):
    with pytest.raises(ModelError) as error:
        ProviderAdapter(endpoint, "key", "model", tokenizer_agreement=True)
    assert error.value.code == "MODEL_CONFIG_INVALID"
    assert "password" not in str(error.value)


@pytest.mark.parametrize("endpoint", ["http://localhost:1234/v1", "http://127.0.0.1:1234/v1"])
def test_explicit_local_provider_proxy_can_use_http(endpoint):
    assert isinstance(
        ProviderAdapter(endpoint, "key", "model", tokenizer_agreement=True), ProviderAdapter
    )


async def test_json_escaped_secret_echo_is_also_discarded():
    escaped = "".join(f"\\u{ord(char):04x}" for char in "SECRET_TEST_KEY")
    responses = iter(
        [
            {"content": '{"ready":true}', "reasoning_content": "ready"},
            {"content": '{"nested":["' + escaped + '"]}', "reasoning_content": "trace"},
        ]
    )

    def handler(request):
        return httpx.Response(
            200, json={"choices": [{"message": next(responses), "finish_reason": "stop"}]}
        )

    live = ProviderAdapter(
        "https://provider.invalid/v1",
        "SECRET_TEST_KEY",
        "model",
        tokenizer_agreement=True,
        transport=httpx.MockTransport(handler),
    )
    await live.probe()
    with pytest.raises(ModelError) as error:
        await live.generate("test", {}, {"type": "object"})
    assert error.value.code == "MODEL_SECRET_ECHO"
