"""Request-only reduction preserves private purpose and actual world facts."""

import json
from copy import deepcopy

from chessx.contexts.budgets import fit_context, request_input_tokens
from chessx.llm.provider import compose_messages
from chessx.llm.validation import TokenCounter


def test_opaque_execution_hashes_are_durable_input_metadata_not_model_knowledge():
    context = {
        "persona": "OWNER_PRIVATE_DESIGN",
        "discipline": "COMMON_DISCIPLINE",
        "discipline_hash": "OPAQUE_DISCIPLINE_AUDIT_HASH",
        "execution_contract": {"expected_state_hash": "OPAQUE_WORLD_AUDIT_HASH"},
    }
    original = deepcopy(context)
    sent = json.dumps(compose_messages("action", context, {}))
    assert "OWNER_PRIVATE_DESIGN" in sent and "COMMON_DISCIPLINE" in sent
    assert "OPAQUE_DISCIPLINE_AUDIT_HASH" not in sent and "OPAQUE_WORLD_AUDIT_HASH" not in sent
    assert context == original
    counter = TokenCounter("cl100k_base")
    without_audit = {
        k: v for k, v in context.items() if k not in {"discipline_hash", "execution_contract"}
    }
    assert request_input_tokens("action", context, {}, counter) == request_input_tokens(
        "action", without_audit, {}, counter
    )


def test_plan_pressure_keeps_purpose_evidence_and_actual_movement_without_mutation():
    own = {"id": "a", "position": {"row": 2, "col": 3}, "type": "rook"}
    context = {
        "persona": "Immutable life story.",
        "goals": "Find the witness.",
        "memory": "The earlier death report was already heard.",
        "observation": {"self": own, "pieces": [deepcopy(own)]},
        "context_channels": {"persona": "descriptive private data " * 100},
        "travel_plan": {
            "id": "plan:a:1",
            "version": 1,
            "status": "active",
            "purpose": "Find the witness.",
            "motive": "Verify the promise.",
            "next_step": "Consider the old meeting place.",
            "target_person_id": "b",
            "desired_position": {"row": 4, "col": 3},
            "evidence_receipt_ids": ["a:1"],
            "history": [{"description": "archived history " * 500}],
            "progress": {
                "current_position": {"row": 2, "col": 3},
                "last_actual_move": {
                    "kind": "MoveCommitted",
                    "source_event_id": "world:" + "event_audit " * 100,
                    "turn": 2,
                    "origin": {"row": 2, "col": 2},
                    "position": {"row": 2, "col": 3},
                    "reached_desired_position": False,
                },
            },
        },
    }
    original = deepcopy(context)
    counter = TokenCounter("cl100k_base")
    fitted = fit_context("action", context, {}, counter, max_output_tokens=512, input_limit=1800)
    assert request_input_tokens("action", fitted, {}, counter, 512) <= 1800
    for key in ("persona", "goals", "memory"):
        assert fitted[key] == original[key]
    for key in (
        "purpose",
        "motive",
        "next_step",
        "target_person_id",
        "desired_position",
        "evidence_receipt_ids",
    ):
        assert fitted["travel_plan"][key] == original["travel_plan"][key]
    move = fitted["travel_plan"]["progress"]["last_actual_move"]
    assert move["origin"] == {"row": 2, "col": 2} and move["position"] == own["position"]
    assert move["turn"] == 2 and move["reached_desired_position"] is False
    assert fitted["observation"]["self"] == own
    assert context == original
