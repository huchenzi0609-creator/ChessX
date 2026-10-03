"""Read models: browser state is never reused as a role prompt."""

from copy import deepcopy

from chessx.contexts import observe

PUBLIC_EVENTS_EXCLUDE = {
    "ActionChosen",
    "MemoryUpdated",
    "MemoryCompactionAdvanced",
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


def conversation_messages(
    conversation: dict, *, before_index: int | None = None, limit: int = 50
) -> dict:
    messages = conversation.get("messages", [])
    limit = min(max(limit, 1), 200)
    end = len(messages) if before_index is None else min(max(before_index, 0), len(messages))
    start = max(0, end - limit)
    return {
        "messages": deepcopy(messages[start:end]),
        "message_page": {
            "total": len(messages),
            "start_index": start,
            "end_index": end,
            "next_before_cursor": start if start > 0 else None,
            "has_earlier": start > 0,
        },
    }


def observer(state: dict, *, compact: bool = False) -> dict:
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
            "pieces",
            "objects",
            "tombstones",
            "battles",
        )
        if key in state
    }
    if compact:
        result["conversations"] = {
            cid: {
                **{
                    key: deepcopy(value)
                    for key, value in conv.items()
                    if key
                    not in {"messages", "counted_message_ids", "proposals", "pending_proposal"}
                },
                **conversation_messages(conv),
            }
            for cid, conv in state.get("conversations", {}).items()
        }
        result["stories"] = {
            sid: {
                **{
                    key: deepcopy(story[key])
                    for key in ("segment_id", "title", "mode", "source_end_seq", "revision")
                    if key in story
                },
                "paragraph_count": len(story.get("paragraphs", [])),
                "content_available": True,
            }
            for sid, story in state.get("stories", {}).items()
        }
    else:
        result["conversations"] = deepcopy(state.get("conversations", {}))
        result["stories"] = deepcopy(state.get("stories", {}))
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


def receipt_page(
    state: dict, pid: str, *, after_receipt: int | None = None, limit: int = 200
) -> dict:
    archive = state["receipts"][pid]
    limit = min(max(limit, 1), 1000)
    start = (
        max(0, len(archive) - limit)
        if after_receipt is None
        else min(max(after_receipt, 0), len(archive))
    )
    end = min(len(archive), start + limit)
    return {
        "receipts": deepcopy(archive[start:end]),
        "receipt_page": {
            "total": len(archive),
            "start_index": start,
            "end_index": end,
            "next_cursor": end if end < len(archive) else None,
            "has_more": end < len(archive),
            "has_earlier": start > 0,
        },
    }


def perspective(
    state: dict, pid: str, *, after_receipt: int | None = None, limit: int = 200
) -> dict:
    return {
        "world_id": state["world_id"],
        "turn": state["turn"],
        "seq": state["seq"],
        "piece_id": pid,
        "observation": observe(state, pid),
        **receipt_page(state, pid, after_receipt=after_receipt, limit=limit),
        "private": deepcopy(state["private"][pid]),
    }
