"""Intention memory is private, cited and descriptive; world physics stay authoritative."""

from copy import deepcopy

import pytest
from chessx.domain.errors import RuleError
from chessx.domain.models import Intent, Scenario
from chessx.domain.scenarios import create_world
from chessx.runtime.plans import (
    PLAN_HISTORY_LIMIT,
    action_schema,
    apply_plan,
    record_plan_progress,
    split_action_result,
    validate_plan,
)
from chessx.runtime.schemas import ACTION
from jsonschema import validate
from pydantic import TypeAdapter


def setup():
    scenario = Scenario.model_validate(
        {
            "scenario_id": "plans",
            "title": "Private plan fixture",
            "board": {"rows": 8, "cols": 8},
            "pieces": [
                {
                    "id": pid,
                    "name": name,
                    "type": "rook",
                    "color": "blue",
                    "position": {"row": 2, "col": col},
                }
                for pid, name, col in [("a", "Alice", 1), ("b", "Bob", 2), ("c", "Hidden", 7)]
            ],
        }
    )
    state = create_world(scenario, {"a": "I have heard of Bob."}, "plans")
    state["turn"] = 1
    state["receipts"]["a"] = [
        {"receipt_id": "a:1", "payload": {"speaker_id": "b"}},
        {"receipt_id": "a:2", "payload": {"speaker_id": "b"}},
    ]
    state["receipts"]["b"] = [{"receipt_id": "b:1"}]
    context = {"observation": {"pieces": [{"id": "b"}]}, "receipts": [state["receipts"]["a"][0]]}
    plan = {
        "decision": "revise",
        "purpose": "Ask Bob about a promise",
        "motive": "I want an answer",
        "next_step": "Walk east to the last place I saw him",
        "target_person_id": "b",
        "desired_position": {"row": 2, "col": 4},
        "evidence_receipt_ids": ["a:1"],
    }
    return state, context, plan


def test_action_plan_schema_is_optional_compact_and_does_not_modify_physics_schema():
    original = deepcopy(ACTION)
    schema = action_schema()
    validate({"kind": "wait"}, schema)
    _, _, plan = setup()
    result = {"kind": "move", "target": {"row": 2, "col": 2}, "private_plan": plan}
    validate(result, schema)
    intent, extracted = split_action_result(result)
    TypeAdapter(Intent).validate_python(intent)
    assert extracted == plan and "private_plan" not in intent
    assert "private_plan" in result and ACTION == original
    assert "private_plan" not in schema["$defs"]["ArrivalAction"]["properties"]


def test_optional_omission_and_keep_preserve_plan_across_turns_without_forcing_a_move():
    state, context, plan = setup()
    apply_plan(state, "a", plan, context)
    before = deepcopy(state["private"]["a"]["travel_plan"])
    state["turn"] = 2
    apply_plan(state, "a", None, context)
    assert state["private"]["a"]["travel_plan"] == before
    apply_plan(state, "a", {"decision": "keep"}, context)
    current = state["private"]["a"]["travel_plan"]
    assert current["version"] == 1 and current["last_review_turn"] == 2
    assert state["pieces"]["a"]["position"] == {"row": 2, "col": 1}
    assert "travel_plan" not in state["private"]["b"]


@pytest.mark.parametrize(
    "change,code",
    [
        ({"evidence_receipt_ids": ["a:2"]}, "PLAN_SOURCE_INVALID"),
        ({"evidence_receipt_ids": ["b:1"]}, "PLAN_SOURCE_INVALID"),
        ({"evidence_receipt_ids": ["plans:1"]}, "PLAN_SOURCE_INVALID"),
        ({"evidence_receipt_ids": ["a:1", "a:1"]}, "PLAN_SOURCE_INVALID"),
        ({"target_person_id": "c"}, "PLAN_TARGET_UNKNOWN"),
        ({"desired_position": {"row": 9, "col": 2}}, "PLAN_POSITION_INVALID"),
        ({"decision": "revise", "purpose": None}, "PLAN_PURPOSE_REQUIRED"),
        ({"decision": "complete"}, "PLAN_NOT_ACTIVE"),
    ],
)
def test_rejected_plan_cannot_mutate_state_or_smuggle_unsupplied_owner_sources(change, code):
    state, context, plan = setup()
    before = deepcopy(state)
    with pytest.raises(RuleError) as caught:
        apply_plan(state, "a", {**plan, **change}, context)
    assert caught.value.code == code and state == before


def test_explicit_archive_citations_and_initial_persona_identity_are_supported():
    state, context, plan = setup()
    context = {"memory_recall": {"long_term": [{"source_receipt_ids": ["a:2"]}]}}
    plan["evidence_receipt_ids"] = ["a:2"]
    assert validate_plan(state, "a", plan, context)["target_person_id"] == "b"
    # This is knowledge of existence; coordinates remain an explicit independently chosen goal.
    assert not state["private"]["a"]["spatial_memory"]["seen_people"]


def test_target_identity_matching_does_not_confuse_substrings_in_persona():
    state, context, plan = setup()
    state["pieces"]["c"]["name"] = "ans"
    state["private"]["a"]["initial_memory"] = "I want an answer."
    plan.update(target_person_id="c", evidence_receipt_ids=[])
    with pytest.raises(RuleError):
        validate_plan(state, "a", plan, {})


def test_proposal_negotiation_and_partial_move_do_not_fabricate_arrival_or_completion():
    state, context, plan = setup()
    apply_plan(state, "a", plan, context)
    events = [
        {"type": "MoveProposed", "actor_id": "a", "payload": {"target": {"row": 2, "col": 4}}},
        {
            "type": "NegotiationResolved",
            "actor_id": "a",
            "payload": {"result": "stop", "position": {"row": 2, "col": 3}},
        },
        {
            "type": "MoveCommitted",
            "actor_id": "a",
            "payload": {"origin": {"row": 2, "col": 1}, "position": {"row": 2, "col": 2}},
        },
    ]
    record_plan_progress(state, events[:2])
    current = state["private"]["a"]["travel_plan"]
    assert "current_position" not in current["progress"]
    # Real event sequence numbers in this test emulate three independently durable steps.
    state["seq"] = 2
    record_plan_progress(state, events[2:])
    progress = current["progress"]["last_actual_move"]
    assert progress["actual_distance"] == 1 and progress["distance_after"] == 2
    assert not progress["reached_attempted_target"] and not progress["reached_desired_position"]
    assert current["status"] == "active"
    assert (
        current["progress"]["last_contact"]["position_scope"]
        == "negotiation_anchor_not_confirmed_arrival"
    )


def test_wait_idempotency_and_bounded_plan_history_keep_retry_and_long_games_small():
    state, context, plan = setup()
    apply_plan(state, "a", plan, context)
    event = {"type": "PieceWaited", "actor_id": "a", "payload": {"piece_id": "a"}}
    record_plan_progress(state, [event])
    record_plan_progress(state, [event])
    assert len(state["private"]["a"]["travel_plan"]["history"]) == 1
    for turn in range(2, 25):
        state.update(turn=turn, seq=turn)
        record_plan_progress(state, [event])
    assert len(state["private"]["a"]["travel_plan"]["history"]) == PLAN_HISTORY_LIMIT


def test_agent_explicitly_revises_and_closes_own_plan_while_preserving_previous_purpose():
    state, context, plan = setup()
    apply_plan(state, "a", plan, context)
    apply_plan(state, "a", {**plan, "purpose": "Check a different question"}, context)
    current = state["private"]["a"]["travel_plan"]
    assert current["version"] == 2 and current["history"][-1]["previous_purpose"] == plan["purpose"]
    apply_plan(state, "a", {"decision": "complete"}, context)
    assert current is not state["private"]["a"]["travel_plan"]
    assert state["private"]["a"]["travel_plan"]["status"] == "complete"
    assert state["pieces"]["a"]["position"] == {"row": 2, "col": 1}


async def test_engineering_fixture_plan_is_explicit_and_uses_the_extended_contract():
    from chessx.llm import FixtureAdapter

    fixture = FixtureAdapter()
    context = {
        "turn": 1,
        "observation": {
            "self": {"id": "a", "type": "rook", "position": {"row": 2, "col": 1}},
            "board": {"rows": 8, "cols": 8},
        },
    }
    result = await fixture.generate("action", context, action_schema(), max_output_tokens=1024)
    intent, plan = split_action_result(result.content)
    assert intent["kind"] == "move"
    assert "工程样例" in plan["purpose"]
    assert plan["desired_position"] == intent["target"]
    context["travel_plan"] = {"status": "active", "purpose": "Earlier fixture plan"}
    result = await fixture.generate("action", context, action_schema(), max_output_tokens=1024)
    _, plan = split_action_result(result.content)
    assert plan == {"decision": "keep"}


def test_optional_null_plan_preserves_previous_intention_without_schema_failure():
    state, context, plan = setup()
    apply_plan(state, "a", plan, context)
    before = deepcopy(state["private"]["a"]["travel_plan"])
    result = {"kind": "wait", "private_plan": None}
    validate(result, action_schema())
    intent, extracted = split_action_result(result)
    assert intent == {"kind": "wait"} and extracted is None
    apply_plan(state, "a", extracted, context)
    assert state["private"]["a"]["travel_plan"] == before


async def test_extended_action_fixture_accepts_actual_unplanned_builder_context():
    from chessx.contexts import build_context
    from chessx.llm import FixtureAdapter

    state, _, _ = setup()
    state["mode"] = "fixture"
    context = build_context(state, "a", "action")
    assert context["travel_plan"] is None
    result = await FixtureAdapter().generate(
        "action", context, action_schema(), max_output_tokens=1024
    )
    intent, plan = split_action_result(result.content)
    validate_plan(state, "a", plan, context)
    assert intent["kind"] in {"move", "wait", "invite"}


@pytest.mark.parametrize(
    "invalid",
    [
        {"kind": "teleport", "target": {"row": 2, "col": 2}},
        {"kind": "move"},
        {"kind": "move", "target": {"row": -1, "col": 2}},
        {"kind": "move", "target": {"row": "2", "col": 2}},
        {"kind": "wait", "unsanctioned": True},
    ],
)
def test_annotation_free_action_contract_retains_every_physical_schema_constraint(invalid):
    from jsonschema import ValidationError

    with pytest.raises(ValidationError):
        validate(invalid, action_schema())


async def test_full_offline_fixture_turn_persists_private_plan_and_actual_progress(tmp_path):
    import asyncio

    from chessx.config import Settings
    from chessx.runtime.runner import WorldRunner
    from chessx.storage import Store

    state, _, _ = setup()
    state.update(mode="fixture", turn=0)
    store = Store(tmp_path / "plans.sqlite3")
    store.create(state)
    runner = WorldRunner(store, Settings(data_dir=tmp_path))
    try:
        await runner.start("plans", {"command_id": "one-turn", "kind": "run", "turns": 1})
        await asyncio.wait_for(runner.tasks["plans"], 5)
        final = store.get("plans")
        assert final["completed_turn"] == 1 and not final.get("error")
        assert final["lifecycle"] == "PAUSED_SEGMENT"
        for owner in final["private"].values():
            assert "工程样例" in owner["travel_plan"]["purpose"]
        assert all(
            "private_plan" not in event.get("payload", {})
            for event in store.events("plans")["events"]
        )
    finally:
        await runner.close()
        store.close()


def test_schema_annotation_removal_does_not_remove_named_object_properties(monkeypatch):
    from chessx.runtime import schemas

    schema = deepcopy(ACTION)
    schema["$defs"]["WaitIntent"]["properties"]["title"] = {
        "type": "string",
        "minLength": 3,
        "title": "A label",
    }
    schema["$defs"]["WaitIntent"]["properties"]["default"] = {"type": "boolean", "default": False}
    monkeypatch.setattr(schemas, "ACTION", schema)
    reduced = action_schema()
    properties = reduced["$defs"]["WaitIntent"]["properties"]
    assert properties["title"] == {"type": "string", "minLength": 3}
    assert properties["default"] == {"type": "boolean"}
