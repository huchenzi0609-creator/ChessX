"""Read models: browser state is never reused as a role prompt."""

from copy import deepcopy

from chessx.contexts import observe

PUBLIC_EVENTS_EXCLUDE = {
    "ActionChosen",
    "MemoryUpdated",
    "GoalUpdated",
    "PrimarySkipped",
    "CursorAdvanced",
    "ReasoningTrace",
    "ModelAttempt",
    "OperationPrepared",
    "OperationFailed",
    "VoteCast",
}


def public_event(event: dict) -> dict | None:
    if event["type"] in PUBLIC_EVENTS_EXCLUDE:
        return None
    value = {
        key: deepcopy(item)
        for key, item in event.items()
        if key not in {"audience", "previous_state_hash", "resulting_state_hash"}
    }
    value["payload"] = {
        key: deepcopy(item)
        for key, item in event.get("payload", {}).items()
        if key
        not in {
            "persona",
            "memory",
            "goals",
            "reasoning",
            "reasoning_content",
            "chain_of_thought",
            "api_key",
            "authorization",
        }
    }
    return value


def observer(state: dict) -> dict:
    result = {
        key: deepcopy(state[key])
        for key in (
            "world_id",
            "title",
            "mode",
            "rule_version",
            "rule_profile",
            "board",
            "turn",
            "completed_turn",
            "seq",
            "generation",
            "lifecycle",
            "phase",
            "usage",
            "error",
            "target_turn",
            "segments",
            "stories",
            "pieces",
            "objects",
            "tombstones",
            "conversations",
            "battles",
        )
        if key in state
    }
    for battle in result["battles"].values():
        battle.pop("narratives", None)
        battle.pop("parent_history", None)
    for conv in result["conversations"].values():
        conv.pop("proposals", None)
        conv.pop("pending_proposal", None)
    for segment in result.get("segments", []):
        segment.pop("story_job", None)
    cursor = state.get("cursor", {})
    result["cursor"] = {key: deepcopy(value) for key, value in cursor.items() if key != "work"}
    if cursor.get("work"):
        result["cursor"]["stage"] = cursor["work"].get("stage")
    return result


def perspective(state: dict, pid: str) -> dict:
    return {
        "world_id": state["world_id"],
        "turn": state["turn"],
        "seq": state["seq"],
        "piece_id": pid,
        "observation": observe(state, pid),
        "receipts": deepcopy(state["receipts"][pid]),
        "private": deepcopy(state["private"][pid]),
    }
