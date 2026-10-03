"""Newest rules remain bounded, source-continuous and independent of legacy archives."""

import json
from collections import Counter
from copy import deepcopy

import pytest
from chessx.domain.models import Scenario
from chessx.domain.profile import frozen_rule_manifest, profile_manifest_hash
from chessx.domain.scenarios import create_world
from chessx.rules.geometry import commit_move, visible_positions
from chessx.runtime.conversations import (
    advance_permission,
    exhausted,
    merge_history,
    message_budget,
    protocol_state,
    record_message,
    update_members,
)
from chessx.runtime.dialogue import DialogueMixin


def world():
    scenario = Scenario.model_validate(
        {
            "schema_version": "2.1",
            "rule_profile": "demo-v2.1",
            "scenario_id": "language",
            "title": "Language fixture",
            "board": {"rows": 8, "cols": 8},
            "pieces": [
                {
                    "id": pid,
                    "name": pid,
                    "type": "knight" if pid == "d" else "rook",
                    "color": "blue",
                    "position": {"row": 3, "col": i + 1},
                }
                for i, pid in enumerate("abcd")
            ],
        }
    )
    state = create_world(scenario, {}, "language")
    state["turn"] = 1
    return state


def conversation(state, cid="c", members=None):
    members = members or ["a", "b"]
    conv = {
        "id": cid,
        "kind": "ordinary",
        "members": list(members),
        "status": "active",
        "counted_message_ids": [],
        "messages": [],
        "pending_proposal": None,
        "last_spoke_turn": {},
        "governance_turn": -1,
    }
    update_members(state, conv, members)
    state["conversations"][cid] = conv
    for pid in members:
        state["pieces"][pid]["conversation_id"] = cid
    return conv


class Harness(DialogueMixin):
    def __init__(self, answer=None):
        self.calls = []
        self.answer = answer

    async def ask(self, state, pid, stage, slot, schema, protocol, validator=None):
        self.calls.append((pid, slot, deepcopy(protocol)))
        answer = self.answer(pid, protocol) if self.answer else {"kind": "say", "speech": slot}
        if validator:
            validator(answer)
        return answer

    def finish_primary(self, state):
        pass


async def drain(runner, state, cid, work=None):
    work = work or {"conversation_id": cid, "stage": "speak"}
    for _ in range(110):
        await runner.ordinary_step(state, cid, work)
        if work.get("done"):
            return
    pytest.fail("Language permissions did not converge")


async def test_ten_speeches_per_world_round_alternate_with_latest_message_and_resume():
    state, runner = world(), Harness()
    conv = conversation(state)
    work = {"conversation_id": "c", "stage": "speak"}
    for _ in range(21):
        await runner.ordinary_step(state, "c", work)
        state = json.loads(json.dumps(state))
        work = json.loads(json.dumps(work))
    conv = state["conversations"]["c"]
    assert work["done"]
    assert Counter(m["speaker_id"] for m in conv["messages"]) == {"a": 10, "b": 10}
    assert [m["speaker_id"] for m in conv["messages"]] == ["a", "b"] * 10
    assert len(set(slot for _, slot, _ in runner.calls)) == 20
    for i, (_, _, protocol) in enumerate(runner.calls):
        if i:
            assert protocol["latest_message"]["message_id"] == conv["messages"][i - 1]["message_id"]
    assert conv["status"] == "active" and conv["max_messages"] == 100
    await drain(runner, state, "c")
    assert len(runner.calls) == 20
    state["turn"] = 2
    await drain(runner, state, "c")
    assert len(runner.calls) == 40


async def test_moving_to_other_conversation_cannot_reset_global_quota():
    state, runner = world(), Harness()
    conversation(state)
    await drain(runner, state, "c")
    conversation(state, "other", ["a", "c"])
    await drain(runner, state, "other")
    assert Counter(pid for pid, _, _ in runner.calls) == {"a": 10, "b": 10, "c": 10}


async def test_pass_waits_for_new_background_but_does_not_silence_followup():
    state = world()
    conversation(state)
    attempts = Counter()

    def answer(pid, protocol):
        attempts[pid] += 1
        if pid == "a" and attempts[pid] == 1:
            return {"kind": "pass"}
        return (
            {"kind": "say", "speech": "new information"} if attempts[pid] <= 2 else {"kind": "pass"}
        )

    runner = Harness(answer)
    await drain(runner, state, "c")
    assert attempts["a"] >= 2
    assert len(runner.calls) <= 24
    final = state["conversations"]["c"]["messages"]
    assert any(m["speaker_id"] == "a" for m in final)


async def test_all_passes_end_once_per_round_and_close_after_two_idle_rounds():
    state, runner = world(), Harness(lambda pid, protocol: {"kind": "pass"})
    conv = conversation(state)
    await drain(runner, state, "c")
    assert len(runner.calls) == 2 and conv["status"] == "active"
    # Same round has no changed background, but a new work object remains bounded.
    await drain(runner, state, "c")
    assert len(runner.calls) <= 4
    state["turn"] = 2
    await drain(runner, state, "c")
    assert conv["status"] == "closed"


def test_merge_budget_uses_real_peak_and_union_history_without_exit_reset():
    state = world()
    left = conversation(state, "l", ["a", "b"])
    right = conversation(state, "r", ["c", "d"])
    left["counted_message_ids"] = ["shared", "left"]
    right["counted_message_ids"] = ["shared", "right"]
    merged = {
        "id": "merged",
        "kind": "ordinary",
        "members": list("abcd"),
        **merge_history([left, right], list("abcd")),
    }
    update_members(state, merged, merged["members"])
    assert set(merged["counted_message_ids"]) == {"shared", "left", "right"}
    assert merged["peak_members"] == 4 and message_budget(state, merged) == 200
    merged["members"] = ["a", "b"]
    update_members(state, merged, merged["members"])
    assert message_budget(state, merged) == 200
    assert len(merged["counted_message_ids"]) == 3


@pytest.mark.parametrize("kind", ["ordinary", "negotiation", "settlement"])
def test_every_language_kind_has_same_identity_budget_latest_and_idle_protocol(kind):
    state = world()
    conv = conversation(state)
    conv["kind"] = kind
    first = record_message(state, conv, "a", "a new fact")
    second = record_message(state, conv, "b", "a short reply")
    assert second["based_on_message_id"] == first["message_id"]
    assert second["id"] == second["message_id"]
    assert protocol_state(state, conv)["remaining_messages"] == 98
    cursor = {}
    for _ in range(4):
        advance_permission(cursor, 2, False)
    assert exhausted(state, conv, cursor)


def test_latest_world_from_legacy_input_and_legacy_knight_geometry_remain_distinct():
    latest = world()
    assert latest["rule_profile"] == "demo-v2.2" and latest["rule_version"] == "2.2"
    assert len(visible_positions(latest, "d")) == 9
    legacy = deepcopy(latest)
    legacy.update(
        rule_version="2.1",
        rule_profile="demo-v2.1",
        rule_manifest=frozen_rule_manifest("demo-v2.1"),
        rule_manifest_hash=profile_manifest_hash("demo-v2.1"),
    )
    assert len(visible_positions(legacy, "d")) == 25
    target = {"row": 5, "col": 5}
    commit_move(latest, "d", target)
    commit_move(legacy, "d", target)
    assert latest["pieces"]["d"]["rest_remaining"] == 2
    assert legacy["pieces"]["d"]["rest_remaining"] == 3
