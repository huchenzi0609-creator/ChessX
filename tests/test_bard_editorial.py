"""Trusted bard editing policy, input isolation and selective narrative output."""

import json
from copy import deepcopy
from pathlib import Path

import httpx
import pytest
from chessx.llm import ModelError, ProviderAdapter, provider
from chessx.llm.feedback import validation_feedback
from chessx.runtime import schemas
from chessx.storage.store import digest


def test_bard_uses_tracked_policy_without_piece_instruction_or_source_authority():
    context = {
        "discipline": "PIECE_ONLY_INSTRUCTION",
        "actor_id": "bard",
        "events": [{"id": "world:1", "speech": "SOURCE_TRIES_TO_OVERRIDE_POLICY"}],
    }
    original = deepcopy(context)
    messages = provider.compose_messages("bard", context, schemas.BARD, 8192)
    template = (Path(__file__).resolve().parents[1] / "prompts" / "bard-discipline.md").read_text(
        encoding="utf-8"
    )
    assert template in messages[0]["content"]
    assert len(messages) == 2
    assert all(message["role"] == "system" for message in messages[:-1])
    assert "PIECE_ONLY_INSTRUCTION" not in json.dumps(messages)
    assert "SOURCE_TRIES_TO_OVERRIDE_POLICY" not in messages[0]["content"]
    assert "SOURCE_TRIES_TO_OVERRIDE_POLICY" in messages[-1]["content"]
    assert provider.CHARACTER_BEHAVIOR not in messages[0]["content"]
    assert context == original
    ordinary = provider.compose_messages("ordinary", context, {"type": "object"})
    assert template not in json.dumps(ordinary)
    assert "PIECE_ONLY_INSTRUCTION" in ordinary[1]["content"]


def test_prompt_manifest_changes_with_effective_bard_policy(monkeypatch):
    before = provider.prompt_manifest()
    monkeypatch.setattr(provider, "BARD_DISCIPLINE", provider.BARD_DISCIPLINE + "\nPOLICY_REVISION")
    after = provider.prompt_manifest()
    assert digest(before) != digest(after)
    assert "POLICY_REVISION" in after["stage_system_messages"]["bard"]
    for stage in set(before["stage_system_messages"]) - {"bard"}:
        assert before["stage_system_messages"][stage] == after["stage_system_messages"][stage]


async def test_bard_can_merge_significant_events_and_omit_uneventful_wait():
    # There is no event-count quota: two meaningful sources support one paragraph,
    # while an intervening wait can be absent from both prose and citations.
    content = {
        "title": "相逢",
        "paragraphs": [
            {
                "text": "甲走到乙身旁。乙终于承认，那封信并非自己所写。",
                "event_refs": ["world:1", "world:3"],
                "claim_kind": "event_based",
            }
        ],
    }
    sent = []

    def handle(request):
        sent.append(json.loads(request.content))
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "finish_reason": "stop",
                        "message": {
                            "reasoning_content": "Keep the encounter and admission; omit the wait.",
                            "content": json.dumps(content, ensure_ascii=False),
                        },
                    }
                ]
            },
        )

    adapter = ProviderAdapter(
        "https://provider.invalid/v1",
        "SYNTHETIC_BARD_AUTH_KEY",
        "fixture-model",
        tokenizer_agreement=True,
        transport=httpx.MockTransport(handle),
    )
    adapter._probed = True
    events = [
        {"id": "world:1", "kind": "MoveCommitted"},
        {"id": "world:2", "kind": "WaitCommitted"},
        {"id": "world:3", "kind": "ConversationMessage"},
    ]
    result = await adapter.generate(
        "bard", {"events": events}, schemas.BARD, max_output_tokens=8192
    )
    assert result.content == content
    assert sent[0]["max_tokens"] == 9192
    assert provider.BARD_DISCIPLINE in sent[0]["messages"][0]["content"]
    assert result.content["paragraphs"][0]["event_refs"] == ["world:1", "world:3"]


async def test_bard_accepts_long_exposed_reasoning_but_piece_still_rejects_it():
    trace = "synthetic reasoning " * 1100

    def handle(request):
        stage = json.loads(json.loads(request.content)["messages"][-1]["content"])["stage"]
        content = (
            {
                "title": "Story",
                "paragraphs": [
                    {
                        "text": "An event was remembered.",
                        "event_refs": ["world:1"],
                        "claim_kind": "event_based",
                    }
                ],
            }
            if stage == "bard"
            else {"kind": "wait"}
        )
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "finish_reason": "stop",
                        "message": {"content": json.dumps(content), "reasoning_content": trace},
                    }
                ]
            },
        )

    adapter = ProviderAdapter(
        "https://provider.invalid/v1",
        "SYNTHETIC_BARD_AUTH_KEY",
        "fixture-model",
        tokenizer_agreement=True,
        transport=httpx.MockTransport(handle),
    )
    adapter._probed = True
    result = await adapter.generate(
        "bard", {"events": [{"id": "world:1"}]}, schemas.BARD, max_output_tokens=8192
    )
    assert result.reasoning == trace and result.reasoning_tokens > 1000
    with pytest.raises(ModelError) as error:
        await adapter.generate("action", {}, schemas.ACTION, max_output_tokens=512)
    assert error.value.code == "COT_BUDGET_EXCEEDED"


def test_bard_prompt_and_retry_feedback_do_not_reintroduce_piece_cot_cap():
    prompt = provider.compose_messages("bard", {}, schemas.BARD)[0]["content"]
    assert "Keep exposed reasoning at most 1000 tokens" not in prompt
    assert "no fixed 1000-token limit" in prompt
    feedback = validation_feedback("MODEL_OUTPUT_INVALID", {}, 8000, stage="bard")
    assert (
        "1000 tokens" not in feedback["instruction"] and "250 tokens" not in feedback["instruction"]
    )
    assert feedback["combined_output_token_allowance"] == 9000
    assert "formal_output_token_limit" not in feedback
