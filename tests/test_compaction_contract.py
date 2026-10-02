"""Memory contracts preserve source boundaries without confusing memories with speech."""

import json
from copy import deepcopy

import httpx
import pytest
from chessx.llm import FixtureAdapter, ModelError, ProviderAdapter
from chessx.llm.provider import (
    CHARACTER_BEHAVIOR,
    STAGE_OUTPUT_LIMITS,
    compose_messages,
    prompt_manifest,
)
from chessx.llm.validation import TokenCounter, validate_output
from chessx.runtime import schemas
from jsonschema import Draft202012Validator


def output():
    return {
        "summary": "曾收到一则消息，其真实性尚待核实。",
        "additions": [
            {
                "kind": "heard_claim",
                "text": "甲曾声称看见乙离开。",
                "salience": "important",
                "source_receipt_ids": ["owner:4"],
            }
        ],
        "goals_markdown": "",
        "goal_decision": "keep",
        "goal_status": "active",
        "reason_summary": "保留本人已获知的消息及其来源。",
    }


@pytest.mark.parametrize(
    "invalid",
    [
        {"summary": "x" * 1401},
        {"additions": [output()["additions"][0]] * 9},
        {"additions": [{**output()["additions"][0], "text": ""}]},
        {"additions": [{**output()["additions"][0], "text": "x" * 301}]},
        {"additions": [{**output()["additions"][0], "source_receipt_ids": []}]},
        {
            "additions": [
                {**output()["additions"][0], "source_receipt_ids": [str(i) for i in range(13)]}
            ]
        },
        {"additions": [{**output()["additions"][0], "source_receipt_ids": ["same", "same"]}]},
        {"additions": [{**output()["additions"][0], "kind": "omniscient_fact"}]},
        {"additions": [{**output()["additions"][0], "unprovided_position": [0, 0]}]},
        {"hidden_reasoning": "not permitted"},
    ],
)
def test_compaction_schema_bounds_entries_and_rejects_undeclared_fields(invalid):
    validator = Draft202012Validator(schemas.COMPACTION)
    validator.validate(output())
    assert list(validator.iter_errors({**output(), **invalid}))


def test_private_long_memory_is_not_limited_as_character_speech():
    content = output()
    content["additions"][0]["text"] = "他向本人报告了一则尚未核实的消息。" * 10
    counter = TokenCounter("cl100k_base")
    assert counter.count(content["additions"][0]["text"]) > 100
    raw = json.dumps(content, ensure_ascii=False)
    assert (
        validate_output(raw, "trace", schemas.COMPACTION, counter, 2048, private_memory=True)
        == content
    )
    with pytest.raises(ModelError) as error:
        validate_output(raw, "trace", schemas.COMPACTION, counter, 2048)
    assert error.value.code == "SPEECH_BUDGET_EXCEEDED"


def test_compaction_privilege_does_not_disable_speech_checks():
    schema = {
        "type": "object",
        "properties": {"speech": {"type": "string"}},
        "required": ["speech"],
    }
    counter = TokenCounter("cl100k_base")
    with pytest.raises(ModelError) as error:
        validate_output(
            json.dumps({"speech": "台词" * 100}, ensure_ascii=False),
            "trace",
            schema,
            counter,
            2048,
            private_memory=True,
        )
    assert error.value.code == "SPEECH_BUDGET_EXCEEDED"


def test_compaction_prompt_keeps_batch_private_and_excludes_character_discipline():
    context = {"actor_id": "owner", "source_batch": ["PRIVATE_BATCH"], "discipline": "DISCIPLINE"}
    before = deepcopy(context)
    messages = compose_messages("memory_compaction", context, schemas.COMPACTION)
    instructions = messages[0]["content"]
    assert "2048 tokens" in instructions
    assert "attributed heard claims" in instructions
    assert "exact receipt IDs PRESENT IN source_batch" in instructions
    assert CHARACTER_BEHAVIOR not in instructions
    assert "PRIVATE_BATCH" not in instructions
    assert "DISCIPLINE" not in "\n".join(message["content"] for message in messages)
    assert "PRIVATE_BATCH" in messages[-1]["content"]
    assert context == before
    assert STAGE_OUTPUT_LIMITS["memory_compaction"] == 2048
    assert "memory_compaction" in prompt_manifest()["stage_system_messages"]


@pytest.mark.asyncio
async def test_fixture_compacts_only_owner_batch_and_retains_incremental_summary():
    fixture = FixtureAdapter()
    context = {
        "previous_summary": "本人此前已知道甲失踪。",
        "source_batch": [
            {
                "receipt_id": "owner:3",
                "turn": 2,
                "channel": "ConversationMessage",
                "actor_id": "messenger",
                "payload": {"speaker_name": "报信者", "speech": "我在北边见过乙。"},
            }
        ],
        "receipts": [{"receipt_id": "different_owner:99", "payload": {"speech": "不可读内容"}}],
    }
    result = await fixture.generate(
        "memory_compaction", context, schemas.COMPACTION, max_output_tokens=2048
    )
    assert result.content["summary"].startswith(context["previous_summary"])
    assert result.content["additions"][0]["source_receipt_ids"] == ["owner:3"]
    assert result.content["additions"][0]["kind"] == "heard_claim"
    assert result.content["additions"][0]["text"].startswith("报信者曾说：")
    assert "不可读内容" not in json.dumps(result.content, ensure_ascii=False)
    assert fixture.counter.count(result.content["summary"]) <= 350
    assert result.output_tokens <= 2048
    assert fixture.mode == "engineering_fixture"


@pytest.mark.asyncio
async def test_mock_provider_compaction_budget_and_private_text_validation():
    content = output()
    content["additions"][0]["text"] = "保存本人听到的消息并注明来源。" * 10
    captured = []

    def handler(request):
        body = json.loads(request.content)
        captured.append(body)
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "finish_reason": "stop",
                        "message": {
                            "content": json.dumps(content, ensure_ascii=False),
                            "reasoning_content": "Brief trace.",
                        },
                    }
                ]
            },
        )

    provider = ProviderAdapter(
        "https://provider.example/v1",
        "test-placeholder",
        "model",
        tokenizer_agreement=True,
        transport=httpx.MockTransport(handler),
    )
    provider._probed = True
    result = await provider.generate(
        "memory_compaction", {}, schemas.COMPACTION, max_output_tokens=2048
    )
    assert result.content == content
    assert captured[0]["max_tokens"] == 3048
