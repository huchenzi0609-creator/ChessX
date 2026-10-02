"""Reflection citation windows are complete, owner-scoped and retry-stable."""

from copy import deepcopy

import pytest
from chessx.contexts.budgets import fit_context
from chessx.contexts.memory import compact_receipt
from chessx.contexts.reflection import reflection_request
from chessx.llm import ModelError
from chessx.llm.validation import TokenCounter
from chessx.runtime import schemas
from jsonschema import Draft202012Validator


def receipt(index, *, owner="a", speech=None):
    return {
        "receipt_id": f"{owner}:{index}",
        "source_event_id": f"world:{index}",
        "channel": "ConversationMessage",
        "payload": {
            "turn": index,
            "actor_id": "b",
            "payload": {"speaker_id": "b", "speech": speech or f"Claim {index}."},
        },
    }


def sample():
    own = [receipt(i) for i in range(1, 12)]
    return {"receipts": {"a": own, "b": [receipt(2, owner="b", speech="OTHER_SECRET")]}}, {
        "actor_id": "a",
        "stage": "memory",
        "persona": "PRIVATE_INITIAL_KNOWLEDGE",
        "memory": "Earlier subjective assessment.",
        "goals": "Find dependable evidence.",
        "observation": {"self": {"position": {"row": 0, "col": 0}}},
        "receipts": own[-2:],
        "memory_recall": {
            "long_term": [
                {
                    "id": "evidence_index",
                    "owner_id": "a",
                    "source_event_id": "world:2",
                    "source_receipt_ids": ["a:2"],
                }
            ],
            "archive_evidence": [compact_receipt(own[3])],
        },
        "evidence_index": {"summary_lines": ["Pointer only [evidence_unprovided]"]},
        "navigation": {
            "known_locations": [
                {
                    "piece_id": "b",
                    "position": {"row": 3, "col": 4},
                    "last_seen_turn": 2,
                    "source_event_id": "world:2",
                    "receipt_id": "a:2",
                }
            ]
        },
        "protocol": {"instruction": "Reflect on your own goals."},
    }


def output(ids):
    return {
        "memory": "A claim was received, but remains uncertain.",
        "goals_markdown": "",
        "goal_decision": "keep",
        "goal_status": "active",
        "reason_summary": "",
        "source_receipt_ids": ids,
    }


def test_projection_pins_complete_owner_evidence_without_duplicate_namespaces():
    state, context = sample()
    projected, schema, ids = reflection_request(state, "a", context)
    assert ids == ["a:2", "a:4", "a:11", "a:10"]
    assert projected["protocol"]["allowed_source_receipt_ids"] == ids
    assert projected["reflection_sources"] == [
        compact_receipt(state["receipts"]["a"][i - 1]) for i in (2, 4, 11, 10)
    ]
    assert all(record["actor_id"] == "b" for record in projected["reflection_sources"])
    assert not {"receipts", "memory_recall", "evidence_index"} & projected.keys()
    location = projected["navigation"]["known_locations"][0]
    assert location == {
        "piece_id": "b",
        "position": {"row": 3, "col": 4},
        "last_seen_turn": 2,
    }
    assert schema["properties"]["source_receipt_ids"]["items"]["enum"] == ids
    Draft202012Validator(schema).validate(output(ids))


@pytest.mark.parametrize("invalid", ["evidence_index", "world:2", "b:2", "a:1", "2"])
def test_hash_event_other_owner_and_unprovided_references_are_rejected(invalid):
    state, context = sample()
    _, schema, ids = reflection_request(state, "a", context)
    assert invalid not in ids
    assert list(Draft202012Validator(schema).iter_errors(output([invalid])))


def test_untrusted_candidates_cannot_expand_the_owners_delivered_archive():
    state, context = sample()
    context["receipts"] = [receipt(2, owner="b"), {"id": "a:1"}]
    context["memory_recall"] = {
        "long_term": [
            {"owner_id": "b", "source_receipt_ids": ["a:2"]},
            {"owner_id": "a", "source_receipt_ids": ["a:99", "world:2", "evidence_index"]},
        ],
        "archive_evidence": [receipt(2, owner="b"), receipt(99)],
    }
    projected, schema, ids = reflection_request(state, "a", context)
    assert ids == [] and projected["reflection_sources"] == []
    validator = Draft202012Validator(schema)
    validator.validate(output([]))
    assert list(validator.iter_errors(output(["a:2"])))


def test_index_pointers_alone_do_not_load_unprovided_archive_records():
    state, context = sample()
    context["receipts"], context["memory_recall"] = [], {}
    context["evidence_index"]["summary_lines"] = ["Previous source [a:2]"]
    projected, _, ids = reflection_request(state, "a", context)
    assert ids == [] and projected["reflection_sources"] == []


def test_a_foreign_owner_with_matching_prefix_is_not_a_canonical_receipt():
    state, context = sample()
    foreign = receipt(2, owner="a:foreign")
    state["receipts"]["a"].append(foreign)
    context["memory_recall"]["archive_evidence"].append(foreign)
    _, schema, ids = reflection_request(state, "a", context)
    assert "a:foreign:2" not in ids
    assert list(Draft202012Validator(schema).iter_errors(output(["a:foreign:2"])))


def test_duplicate_candidate_ids_and_identical_archive_records_are_deduplicated():
    state, context = sample()
    state["receipts"]["a"].append(deepcopy(state["receipts"]["a"][1]))
    context["memory_recall"]["long_term"][0]["source_receipt_ids"] = ["a:2"] * 3
    context["memory_recall"]["archive_evidence"].append(receipt(2))
    projected, schema, ids = reflection_request(state, "a", context)
    assert ids.count("a:2") == 1
    assert len(projected["reflection_sources"]) == len(ids)
    assert list(Draft202012Validator(schema).iter_errors(output(["a:2", "a:2"])))


def test_conflicting_duplicate_receipt_keys_are_not_resolved_arbitrarily():
    state, context = sample()
    state["receipts"]["a"].append(receipt(2, speech="Conflicting delivered content."))
    projected, _, ids = reflection_request(state, "a", context)
    assert "a:2" not in ids
    assert all(record["receipt_id"] != "a:2" for record in projected["reflection_sources"])


def test_source_window_is_bounded_without_modifying_input_archive_or_schema():
    state, context = sample()
    before_state, before_context, before_schema = (
        deepcopy(state),
        deepcopy(context),
        deepcopy(schemas.MEMORY),
    )
    projected, schema, ids = reflection_request(state, "a", context, max_sources=2)
    assert ids == ["a:2", "a:4"] and len(projected["reflection_sources"]) == 2
    assert state == before_state and context == before_context and schemas.MEMORY == before_schema
    projected["reflection_sources"][0]["payload"]["speech"] = "Independent DTO"
    schema["properties"]["source_receipt_ids"]["items"]["enum"].clear()
    assert state == before_state and context == before_context and schemas.MEMORY == before_schema


def test_budget_fitting_and_retry_feedback_preserve_complete_pinned_sources():
    state, context = sample()
    projected, schema, ids = reflection_request(state, "a", context)
    projected["protocol"]["validation_feedback"] = {"code": "MEMORY_SOURCE_INVALID"}
    pinned = deepcopy(projected["reflection_sources"])
    counter = TokenCounter("cl100k_base")
    fitted = fit_context("memory", projected, schema, counter, max_output_tokens=1024)
    assert fitted["reflection_sources"] == pinned
    assert fitted["protocol"]["allowed_source_receipt_ids"] == ids
    with pytest.raises(ModelError) as error:
        fit_context("memory", projected, schema, counter, max_output_tokens=1024, input_limit=20)
    assert error.value.code == "CONTEXT_BUDGET_EXCEEDED"
    assert projected["reflection_sources"] == pinned


@pytest.mark.parametrize("maximum", [-1, True, 1.5])
def test_invalid_source_window_limits_are_rejected(maximum):
    state, context = sample()
    with pytest.raises(ValueError):
        reflection_request(state, "a", context, max_sources=maximum)
