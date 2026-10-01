import hashlib
import importlib.util
import io
import json
from pathlib import Path
from zipfile import ZipFile

import httpx
import pytest
from chessx.llm import ModelError, ProviderAdapter
from chessx.llm.validation import TokenCounter
from tokenizers import Tokenizer
from tokenizers.models import WordLevel
from tokenizers.pre_tokenizers import Whitespace
from tokenizers.processors import TemplateProcessing


def setup_module():
    path = Path(__file__).resolve().parents[1] / "scripts" / "setup_deepseek_tokenizer.py"
    spec = importlib.util.spec_from_file_location("chessx_tokenizer_setup", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def tokenizer_json():
    tokenizer = Tokenizer(
        WordLevel({"[UNK]": 0, "hello": 1, "world": 2, "[BOS]": 3}, unk_token="[UNK]")
    )
    tokenizer.pre_tokenizer = Whitespace()
    tokenizer.post_processor = TemplateProcessing(single="[BOS] $A", special_tokens=[("[BOS]", 3)])
    return tokenizer.to_str().encode()


def archive(entries):
    result = io.BytesIO()
    with ZipFile(result, "w") as bundle:
        for path, payload in entries.items():
            bundle.writestr(path, payload)
    return result.getvalue()


def install_fixture(tmp_path):
    module = setup_module()
    payload = tokenizer_json()
    manifest = module.install_archive(
        archive(
            {
                "official/tokenizer.json": payload,
                "official/tokenizer.py": "raise RuntimeError('MUST_NOT_EXECUTE')",
            }
        ),
        tmp_path,
    )
    return manifest, tmp_path / "tokenizer.json"


def test_deepseek_counter_uses_local_json_exact_ids_without_padding_or_bos(tmp_path):
    manifest, path = install_fixture(tmp_path)
    counter = TokenCounter("deepseek_v4", tokenizer_path=path)
    assert counter.encoding.name == "deepseek_v4"
    assert counter.count("hello world") == 2
    assert counter.count("hello") == 1
    assert counter.count("") == 0
    assert counter.count("unknown unknown") == 2
    assert manifest["tokenizer_sha256"] == hashlib.sha256(path.read_bytes()).hexdigest()
    assert sorted(p.name for p in tmp_path.iterdir()) == ["manifest.json", "tokenizer.json"]


def test_counter_rejects_missing_or_modified_asset_and_unverified_provenance(tmp_path):
    with pytest.raises(ModelError) as error:
        TokenCounter("deepseek_v4", tokenizer_path=tmp_path / "missing.json")
    assert error.value.code == "TOKENIZER_UNAVAILABLE"
    _, path = install_fixture(tmp_path)
    original = path.read_bytes()
    path.write_bytes(original + b" ")
    with pytest.raises(ModelError):
        TokenCounter("deepseek_v4", tokenizer_path=path)
    path.write_bytes(original)
    manifest_path = tmp_path / "manifest.json"
    manifest = json.loads(manifest_path.read_text())
    manifest["source_url"] = "https://unverified.invalid/tokenizer.zip"
    manifest_path.write_text(json.dumps(manifest))
    with pytest.raises(ModelError):
        TokenCounter("deepseek_v4", tokenizer_path=path)


def test_setup_ignores_executable_zip_members_and_cannot_escape_destination(tmp_path):
    module = setup_module()
    module.install_archive(
        archive(
            {
                "../../tokenizer.json": tokenizer_json(),
                "../outside.py": "raise RuntimeError('MUST_NOT_EXECUTE')",
            }
        ),
        tmp_path / "cache",
    )
    assert sorted(p.name for p in (tmp_path / "cache").iterdir()) == [
        "manifest.json",
        "tokenizer.json",
    ]
    assert not (tmp_path / "outside.py").exists()
    assert not (tmp_path / "tokenizer.json").exists()


@pytest.mark.parametrize(
    "entries",
    [
        {"tokenizer.py": "raise RuntimeError('MUST_NOT_EXECUTE')"},
        {"a/tokenizer.json": tokenizer_json(), "b/tokenizer.json": tokenizer_json()},
        {"tokenizer.json": b'{"not_a_valid_tokenizer": true}'},
    ],
)
def test_setup_rejects_missing_ambiguous_or_invalid_json(tmp_path, entries):
    with pytest.raises(ValueError):
        setup_module().install_archive(archive(entries), tmp_path)
    assert not (tmp_path / "tokenizer.json").exists()


async def test_explicit_deepseek_profile_requests_low_thinking_and_json_mode():
    requests = []

    def handler(request):
        requests.append(request)
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

    adapter = ProviderAdapter(
        "https://api.deepseek.com/v1",
        "SYNTHETIC_DEEPSEEK_KEY",
        "deepseek-flash",
        tokenizer_agreement=True,
        provider_profile="deepseek",
        response_format="json_object",
        transport=httpx.MockTransport(handler),
    )
    await adapter.probe()
    body = json.loads(requests[0].content)
    assert body["thinking"] == {"type": "enabled"}
    assert body["reasoning_effort"] == "low"
    assert body["response_format"] == {"type": "json_object"}
    assert body["max_tokens"] == 384
    assert '{\\"ready\\":true}' in body["messages"][-1]["content"]
    assert "SYNTHETIC_DEEPSEEK_KEY" not in requests[0].content.decode()
    assert not any(key in body for key in ["temperature", "frequency_penalty", "presence_penalty"])


def test_deepseek_profile_does_not_guess_from_model_name_or_override_format():
    generic = ProviderAdapter(
        "https://api.deepseek.com/v1", "key", "deepseek-flash", tokenizer_agreement=True
    )
    assert generic.provider_profile == "generic" and generic.response_format == "json_schema"
    with pytest.raises(ModelError) as error:
        ProviderAdapter(
            "https://api.deepseek.com/v1",
            "key",
            "deepseek-flash",
            tokenizer_agreement=True,
            provider_profile="deepseek",
            response_format="json_schema",
        )
    assert error.value.code == "MODEL_CONFIG_INVALID"
