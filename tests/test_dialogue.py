from copy import deepcopy

import pytest
from chessx.llm import ModelError
from chessx.runtime.dialogue import DialogueMixin


def world():
    return {
        "turn": 1,
        "board": {"rows": 8, "cols": 8},
        "limits": {"conversation_max_messages": 50},
        "pieces": {
            pid: {
                "id": pid,
                "type": "rook",
                "alive": True,
                "position": {"row": 1, "col": i + 1},
                "rest_remaining": 0,
                "battle_id": None,
                "conversation_id": None,
            }
            for i, pid in enumerate("abcd")
        },
        "conversations": {},
    }


def conversation(state, cid, members, counted=None):
    state["conversations"][cid] = {
        "id": cid,
        "kind": "ordinary",
        "status": "active",
        "members": members,
        "counted_message_ids": counted or [],
        "messages": [],
        "pending_proposal": None,
        "kick_used_by": [],
        "last_spoke_turn": {},
        "governance_turn": -1,
        "idle_turns": 0,
        "last_idle_turn": -1,
        "last_activity_turn": -1,
    }
    for pid in members:
        state["pieces"][pid]["conversation_id"] = cid
    return state["conversations"][cid]


class Harness(DialogueMixin):
    def __init__(self, answers=None):
        self.answers = answers or {}
        self.calls = []
        self.finished = 0

    def finish_primary(self, state):
        self.finished += 1

    async def ask(self, state, pid, stage, slot, schema, protocol, validator=None):
        self.calls.append((pid, stage, deepcopy(protocol)))
        answer = self.answers.get(
            (pid, stage),
            {"stay": True}
            if stage == "invite_response"
            else {"approve": True}
            if stage == "vote"
            else {"kind": "pass"},
        )
        if isinstance(answer, Exception):
            raise answer
        if validator:
            validator(answer)
        return answer


async def drain_invite(runner, state, work):
    events = []
    for _ in range(15):
        events.extend(await runner.invite_step(state, work))
        if work.get("done"):
            return events
    raise AssertionError("invitation failed to terminate")


async def drain_ordinary(runner, state, cid, work=None):
    work = work or {"conversation_id": cid, "stage": "speak", "member_index": 0}
    events = []
    for _ in range(40):
        events.extend(await runner.ordinary_step(state, cid, work))
        if work.get("done"):
            return events
    raise AssertionError("ordinary conversation failed to terminate")


async def test_invitation_response_before_first_speech_and_exit():
    state, runner = world(), Harness({("b", "invite_response"): {"stay": False}})
    work = {"actor_id": "a", "intent": {"kind": "invite", "target_id": "b"}, "stage": "init"}
    events = await drain_invite(runner, state, work)
    assert [stage for _, stage, _ in runner.calls] == ["invite_response"]
    assert runner.finished == 1
    assert state["conversations"][work["conversation_id"]]["status"] == "closed"
    assert all(event["type"] != "ConversationMessage" for event in events)
    assert state["pieces"]["a"]["conversation_id"] is None


async def test_merge_budget_is_union_and_no_transcripts_backfilled():
    state, runner = world(), Harness()
    left = conversation(state, "left", ["a", "b"], ["same", "one"])
    right = conversation(state, "right", ["c", "d"], ["same", "two"])
    left["messages"] = [{"speech": "PRIVATE_LEFT_OLD"}]
    right["messages"] = [{"speech": "PRIVATE_RIGHT_OLD"}]
    left["kick_used_by"] = ["a"]
    left["last_spoke_turn"] = {"a": 1}
    state["pieces"]["c"]["position"] = {"row": 1, "col": 2}
    work = {"actor_id": "a", "intent": {"kind": "invite", "target_id": "c"}, "stage": "init"}
    await drain_invite(runner, state, work)
    merged = state["conversations"][work["conversation_id"]]
    assert set(merged["counted_message_ids"]) == {"same", "one", "two"}
    assert merged["messages"] == []
    assert merged["kick_used_by"] == ["a"]
    assert merged["last_spoke_turn"] == {"a": 1}
    assert [pid for pid, _, _ in runner.calls] == ["c", "d"]


async def test_fifty_messages_on_merge_closes_parents():
    state, runner = world(), Harness()
    conversation(state, "left", ["a", "b"], [f"m{i}" for i in range(49)])
    conversation(state, "right", ["c", "d"], ["last"])
    state["pieces"]["c"]["position"] = {"row": 1, "col": 2}
    work = {"actor_id": "a", "intent": {"kind": "invite", "target_id": "c"}, "stage": "init"}
    await drain_invite(runner, state, work)
    assert work["done"]
    assert len(state["conversations"]) == 2
    assert all(conv["status"] == "closed" for conv in state["conversations"].values())
    assert not runner.calls


async def test_one_permission_per_member_and_two_idle_world_turns():
    state, runner = world(), Harness()
    conversation(state, "c", ["a", "b"])
    await drain_ordinary(runner, state, "c")
    assert len(runner.calls) == 2
    await drain_ordinary(runner, state, "c")
    assert len(runner.calls) == 2
    assert state["conversations"]["c"]["status"] == "active"
    state["turn"] = 2
    await drain_ordinary(runner, state, "c")
    assert len(runner.calls) == 4
    assert state["conversations"]["c"]["status"] == "closed"


async def test_two_member_kick_is_noop_and_consumes_quota():
    state = world()
    runner = Harness({("a", "ordinary"): {"kind": "propose_kick", "target_id": "b"}})
    conv = conversation(state, "c", ["a", "b"])
    events = await drain_ordinary(runner, state, "c")
    assert conv["members"] == ["a", "b"]
    assert conv["kick_used_by"] == ["a"]
    assert not any(stage == "vote" for _, stage, _ in runner.calls)
    assert any(
        event["type"] == "GovernanceResolved" and event["payload"].get("result") == "not_approved"
        for event in events
    )


async def test_failed_vote_does_not_become_rejection():
    state = world()
    runner = Harness(
        {
            ("a", "ordinary"): {"kind": "propose_kick", "target_id": "b"},
            ("c", "vote"): ModelError("MODEL_TIMEOUT", "timeout"),
        }
    )
    conv = conversation(state, "c", ["a", "b", "c"])
    work = {"conversation_id": "c", "stage": "speak", "member_index": 0}
    await runner.ordinary_step(state, "c", work)
    with pytest.raises(ModelError):
        await runner.ordinary_step(state, "c", work)
    assert conv["pending_proposal"]["votes"] == {}
    assert work["vote_index"] == 0


async def test_fiftieth_message_closes_before_governance():
    state = world()
    runner = Harness({("a", "ordinary"): {"kind": "say", "speech": "Final message"}})
    conv = conversation(state, "c", ["a", "b"], [f"m{i}" for i in range(49)])
    events = await drain_ordinary(runner, state, "c")
    assert len(conv["counted_message_ids"]) == 50
    assert conv["status"] == "closed"
    assert events[0]["audience"] == ["a", "b"]
    assert len(runner.calls) == 1


async def test_approved_add_merges_group_and_preserves_permission_quotas():
    state = world()
    runner = Harness({("a", "ordinary"): {"kind": "propose_add", "target_id": "c"}})
    conversation(state, "left", ["a", "b"], ["old_left"])
    right = conversation(state, "right", ["c", "d"], ["old_right"])
    right["last_spoke_turn"] = {"d": 1}
    state["pieces"]["c"]["position"] = {"row": 1, "col": 2}
    work = {"conversation_id": "left", "stage": "speak", "member_index": 0}
    await drain_ordinary(runner, state, "left", work)
    new_id = state["pieces"]["a"]["conversation_id"]
    merged = state["conversations"][new_id]
    assert new_id not in {"left", "right"}
    assert merged["members"] == ["a", "b", "c", "d"]
    assert set(merged["counted_message_ids"]) == {"old_left", "old_right"}
    assert [pid for pid, stage, _ in runner.calls if stage == "ordinary"] == ["a", "b", "c"]
    assert [pid for pid, stage, _ in runner.calls if stage == "vote"] == ["a", "b"]
    votes = [protocol for _, stage, protocol in runner.calls if stage == "vote"]
    assert all(protocol["proposal"]["member_ids"] == ["c", "d"] for protocol in votes)
    assert runner.finished == 0


async def test_membership_change_cancels_pending_governance():
    state = world()
    runner = Harness({("a", "ordinary"): {"kind": "propose_kick", "target_id": "b"}})
    conv = conversation(state, "conv", ["a", "b", "c"])
    work = {"conversation_id": "conv", "stage": "speak", "member_index": 0}
    await runner.ordinary_step(state, "conv", work)
    conv["members"] = ["a", "b"]
    events = await runner.ordinary_step(state, "conv", work)
    assert conv["pending_proposal"] is None
    assert events[0]["payload"]["reason"] == "membership_changed"
    assert not any(stage == "vote" for _, stage, _ in runner.calls)
