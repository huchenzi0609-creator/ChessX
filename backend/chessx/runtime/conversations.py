"""Shared durable language protocol: identity, budgets, permissions and idle cycles.

Adapters retain ordinary governance, contact outcomes and settlement rights.
All three use these state transitions, keeping model latency outside mutation.
"""

from copy import deepcopy
from uuid import uuid4

from chessx.domain.profile import conversation_message_budget, is_legacy_rules


def message_count(conversation: dict) -> int:
    return len(set(conversation.get("counted_message_ids", [])))


def update_members(state: dict, conversation: dict, members: list[str]) -> int:
    conversation["peak_members"] = max(conversation.get("peak_members", 0), len(members))
    conversation["participant_ids"] = list(
        dict.fromkeys(conversation.get("participant_ids", []) + members)
    )
    conversation["max_messages"] = message_budget(state, conversation)
    return conversation["max_messages"]


def message_budget(state: dict, conversation: dict) -> int:
    return conversation_message_budget(state, conversation)


def merge_history(parents: list[dict], members: list[str]) -> dict:
    return {
        "counted_message_ids": sorted(
            set().union(*(set(p.get("counted_message_ids", [])) for p in parents))
        ),
        "peak_members": max(
            [len(members)] + [p.get("peak_members", len(p["members"])) for p in parents]
        ),
        "participant_ids": list(
            dict.fromkeys(
                members + [pid for p in parents for pid in p.get("participant_ids", p["members"])]
            )
        ),
    }


def protocol_state(state: dict, conversation: dict) -> dict:
    count = message_count(conversation)
    cap = update_members(state, conversation, conversation["members"])
    return {
        "conversation_id": conversation["id"],
        "kind": conversation["kind"],
        "member_ids": list(conversation["members"]),
        "message_count": count,
        "max_messages": cap,
        "remaining_messages": max(0, cap - count),
        "peak_members": conversation["peak_members"],
        "latest_message": deepcopy(conversation.get("messages", [])[-1])
        if conversation.get("messages")
        else None,
        "conversation_version": conversation.get(
            "speech_version", len(conversation.get("messages", []))
        ),
    }


def record_message(state: dict, conversation: dict, pid: str, speech: str) -> dict:
    """One canonical identity is appended once and counted across merged ancestors."""
    mid = f"message_{uuid4().hex}"
    latest = conversation.get("messages", [])[-1] if conversation.get("messages") else {}
    version = conversation.get("speech_version", len(conversation.get("messages", [])))
    message = {
        "id": mid,
        "message_id": mid,
        "conversation_id": conversation["id"],
        "speaker_id": pid,
        "speaker_name": state["pieces"][pid].get("name", pid),
        "speech": speech,
        "turn": state["turn"],
        "based_on_message_id": latest.get("message_id", latest.get("id")),
        "conversation_version": version,
    }
    conversation.setdefault("messages", []).append(deepcopy(message))
    conversation.setdefault("counted_message_ids", []).append(mid)
    conversation["message_count"] = message_count(conversation)
    conversation["speech_version"] = version + 1
    conversation["last_activity_turn"] = state["turn"]
    conversation["last_active_turn"] = state["turn"]
    return message


def advance_permission(
    cursor: dict,
    members_count: int,
    spoke: bool,
    *,
    index_key="permission_index",
    speech_key="cycle_has_speech",
) -> None:
    cursor[index_key] = cursor.get(index_key, 0) + 1
    cursor[speech_key] = cursor.get(speech_key, False) or spoke
    if cursor[index_key] % members_count == 0:
        cursor["idle_cycles"] = 0 if cursor[speech_key] else cursor.get("idle_cycles", 0) + 1
        cursor[speech_key] = False


def exhausted(state: dict, conversation: dict, cursor: dict | None = None) -> bool:
    return message_count(conversation) >= message_budget(state, conversation) or (
        cursor is not None and cursor.get("idle_cycles", 0) >= 2
    )


def finish_protocol(state: dict, conversation: dict) -> None:
    conversation.update(status="closed", last_active_turn=state["turn"], closed_turn=state["turn"])


def advance_idle_world_turn(state: dict, conversation: dict) -> bool:
    if conversation.get("last_idle_turn") != state["turn"]:
        active = conversation.get("last_activity_turn") == state["turn"]
        conversation["idle_turns"] = 0 if active else conversation.get("idle_turns", 0) + 1
        conversation["last_idle_turn"] = state["turn"]
    return conversation["idle_turns"] >= 2


def ordinary_usage(state: dict, pid: str) -> dict:
    ledger = state.setdefault("dialogue_permissions", {"turn": state["turn"], "actors": {}})
    if ledger["turn"] != state["turn"]:
        ledger.update(turn=state["turn"], actors={})
    return ledger["actors"].setdefault(pid, {"speeches": 0, "permissions": 0})


def ordinary_eligible(state: dict, conversation: dict, pid: str, work: dict) -> bool:
    if is_legacy_rules(state):
        return (
            pid not in work.get("processed_members", [])
            and conversation.get("last_spoke_turn", {}).get(pid) != state["turn"]
        )
    usage = ordinary_usage(state, pid)
    return (
        usage["speeches"] < 10
        and usage["permissions"] < 12
        and work.get("passed_at_version", {}).get(pid)
        != conversation.get("speech_version", len(conversation.get("messages", [])))
    )


def ordinary_slot(state: dict, cid: str, pid: str) -> str:
    prefix = f"turn:{state['turn']}:ordinary:{cid}:{pid}"
    if is_legacy_rules(state):
        return prefix
    return f"{prefix}:permission:{ordinary_usage(state, pid)['permissions']}"


def ordinary_permission_context(state: dict, pid: str, conversation: dict) -> dict:
    usage = ordinary_usage(state, pid)
    return {
        "remaining_speeches_this_world_turn": 1
        if is_legacy_rules(state)
        else max(0, 10 - usage["speeches"]),
        "remaining_permissions_this_world_turn": 1
        if is_legacy_rules(state)
        else max(0, 12 - usage["permissions"]),
        "governance_available": conversation.get("governance_turn", -1) != state["turn"],
    }
