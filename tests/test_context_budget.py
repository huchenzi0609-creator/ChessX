from copy import deepcopy

import pytest
from chessx.contexts.budgets import fit_context, request_input_tokens
from chessx.llm import ModelError
from chessx.llm.validation import TokenCounter


@pytest.fixture
def counter():
    return TokenCounter("cl100k_base")


def receipt(index):
    return {
        "receipt_id": f"self:{index}",
        "source_event_id": f"w:{index}",
        "payload": {"speech": "history " * 400},
    }


def test_small_context_is_preserved_and_copied(counter):
    original = {
        "persona": "Anchor",
        "goals": "Goal",
        "memory": "Private memory",
        "receipts": [{"receipt_id": "r1"}],
        "observation": {"self": {"id": "a"}},
    }
    fitted = fit_context("action", original, {"type": "object"}, counter, max_output_tokens=512)
    assert fitted == original and fitted is not original
    fitted["receipts"][0]["receipt_id"] = "edited"
    assert original["receipts"][0]["receipt_id"] == "r1"


def test_receipt_window_keeps_latest_and_source_identity_without_touching_archive(counter):
    original = {
        "persona": "Anchor",
        "goals": "Goal",
        "memory": "Memory",
        "receipts": [receipt(i) for i in range(30)],
    }
    snapshot = deepcopy(original)
    fitted = fit_context("memory", original, {"type": "object"}, counter, max_output_tokens=1024)
    assert 1 <= len(fitted["receipts"]) < len(original["receipts"])
    assert fitted["receipts"] == original["receipts"][-len(fitted["receipts"]) :]
    assert fitted["receipts"][-1]["source_event_id"] == "w:29"
    assert original == snapshot
    assert request_input_tokens("memory", fitted, {"type": "object"}, counter) <= 6144


def test_protocol_transcript_windows_only_with_live_state_and_latest_proposals(counter):
    original = {
        "persona": "Anchor",
        "observation": {"self": {"id": "a"}},
        "protocol": {
            "latest_proposals": {"a": "stop", "b": "undecided"},
            "position": {"row": 1, "col": 2},
            "messages": [{"id": f"m{i}", "speech": "history " * 400} for i in range(20)],
        },
    }
    fitted = fit_context(
        "negotiation", original, {"type": "object"}, counter, max_output_tokens=256
    )
    assert 1 <= len(fitted["protocol"]["messages"]) < 20
    assert fitted["protocol"]["messages"][-1] == original["protocol"]["messages"][-1]
    assert fitted["protocol"]["latest_proposals"] == original["protocol"]["latest_proposals"]
    assert fitted["observation"] == original["observation"]
    assert len(original["protocol"]["messages"]) == 20


@pytest.mark.parametrize("missing", ["latest_proposals", "current_state"])
def test_transcript_without_recoverable_current_protocol_cannot_be_dropped(counter, missing):
    context = {
        "observation": {"self": {"id": "a"}},
        "protocol": {
            "latest_proposals": {"a": "stop"},
            "messages": [{"speech": "history " * 5000}, {"speech": "latest"}],
        },
    }
    if missing == "latest_proposals":
        context["protocol"].pop("latest_proposals")
    else:
        context.pop("observation")
    with pytest.raises(ModelError) as error:
        fit_context("negotiation", context, {"type": "object"}, counter, max_output_tokens=256)
    assert error.value.code == "CONTEXT_BUDGET_EXCEEDED"


@pytest.mark.parametrize("field", ["persona", "goals", "memory", "participant_memories"])
def test_essential_character_or_narrator_inputs_never_silently_cut(counter, field):
    value = (
        [{"owner_id": "a", "memory": "essential " * 15000}]
        if field == "participant_memories"
        else "essential " * 5000
    )
    context = {field: value, "receipts": [receipt(1), receipt(2)]}
    original = deepcopy(context)
    with pytest.raises(ModelError) as error:
        fit_context(
            "battle_narrator" if field == "participant_memories" else "action",
            context,
            {"type": "object"},
            counter,
            max_output_tokens=512,
        )
    assert error.value.code == "CONTEXT_BUDGET_EXCEEDED"
    assert "essential" not in error.value.message
    assert context == original


def test_model_capacity_includes_formal_output_cot_and_framing(counter):
    context, schema = {"persona": "Anchor"}, {"type": "object"}
    inputs = request_input_tokens("action", context, schema, counter)
    assert (
        fit_context(
            "action",
            context,
            schema,
            counter,
            max_output_tokens=512,
            model_capacity=inputs + 512 + 1000,
        )
        == context
    )
    with pytest.raises(ModelError):
        fit_context(
            "action",
            context,
            schema,
            counter,
            max_output_tokens=512,
            model_capacity=inputs + 512 + 1000 - 1,
        )


def test_stage_budget_differs_without_discarding_essential_text(counter):
    context = {"memory": "remembered " * 4500}
    with pytest.raises(ModelError):
        fit_context("surrender", context, {}, counter, max_output_tokens=128)
    assert fit_context("memory", context, {}, counter, max_output_tokens=1024) == context
