"""Field-whitelisted observations and commit-time delivery projections.

Returning fresh DTOs prevents downstream agents from accessing internal state
through shared references. Language is delivered only to explicit audiences.
"""

from copy import deepcopy

PIECE_FIELDS = (
    "id",
    "name",
    "type",
    "color",
    "position",
    "alive",
    "rest_remaining",
    "conversation_id",
    "battle_id",
)
EVENT_FIELDS = ("id", "event_id", "world_id", "seq", "turn", "type", "kind", "actor_id")
PRIVATE_EVENTS = {
    "MemoryUpdated",
    "GoalUpdated",
    "ReasoningTrace",
    "ModelAttempt",
    "OperationPrepared",
    "OperationFailed",
    "StoryGenerated",
}
LANGUAGE_EVENTS = {
    "ConversationMessage",
    "ConversationJoined",
    "ConversationLeft",
    "ConversationCreated",
    "NegotiationMessage",
    "SettlementMessage",
    "Invitation",
    "TransferOffered",
    "TransferAccepted",
    "TransferRejected",
    "BattleOutcome",
    "Surrendered",
    "ObjectTransferOffered",
    "ObjectTransferRejected",
    "ObjectTransferred",
    "OfferCancelled",
    "MembershipChanged",
    "ConversationClosed",
    "ConversationMerged",
    "InvitationResponded",
    "GovernanceProposal",
    "GovernanceResolved",
    "LoserReleased",
    "VoteCast",
    "InvitationUnavailable",
    "ConversationPassed",
    "BattleOutcomeCommitted",
    "SurrenderDeclared",
    "CombatContinued",
    "SettlementStarted",
    "ReleaseProposed",
    "ReleaseVoteCast",
    "ReleaseProposalRejected",
    "SettlementPermissionPassed",
    "SettlementTerminalStarted",
    "NegotiationResolved",
    "NegotiationPermissionUsed",
}
STRUCTURE_FIELDS = {
    "piece_id",
    "actor_id",
    "name",
    "position",
    "from",
    "to",
    "actual_to",
    "color",
    "label",
    "object_id",
    "object_name",
    "description",
    "location",
    "battle_id",
    "anchor",
    "status",
    "round",
}
LANGUAGE_FIELDS = {
    "conversation_id",
    "battle_id",
    "actor_id",
    "speaker_id",
    "piece_id",
    "member_ids",
    "participants",
    "speech",
    "text",
    "kind",
    "proposal",
    "result",
    "target_id",
    "sender_id",
    "recipient_id",
    "object_id",
    "object_name",
    "description",
    "winners",
    "losers",
    "surrendered",
    "status",
    "change",
    "reason",
    "offer_id",
    "parent_ids",
    "approvals",
    "members",
    "message_id",
    "proposal_id",
    "approve",
    "position",
    "mode",
    "decision_party",
    "winner_ids",
    "loser_ids",
    "loser_id",
    "terminal",
}


def _plain(value) -> dict:
    return value.model_dump() if hasattr(value, "model_dump") else value


def _items(value) -> list[dict]:
    return list(value.values()) if isinstance(value, dict) else list(value or [])


def _piece(piece: dict) -> dict:
    return {key: deepcopy(piece[key]) for key in PIECE_FIELDS if key in piece}


def _in_view(piece: dict, position: dict) -> bool:
    radius = 2 if piece.get("type") == "knight" else 1
    own = piece["position"]
    return (
        abs(own["row"] - position["row"]) <= radius and abs(own["col"] - position["col"]) <= radius
    )


def observe(state, pid: str) -> dict:
    state = _plain(state)
    pieces = state["pieces"]
    own = pieces[pid] if isinstance(pieces, dict) else next(p for p in pieces if p["id"] == pid)
    result = {
        "self": _piece(own),
        "board": deepcopy(state["board"]),
        "visible_cells": [],
        "pieces": [],
        "objects": [],
        "tombstones": [],
    }
    rest_snapshot = state.get("rest_at_start", {}).get(pid, own.get("rest_remaining", 0))
    result["self"]["rest_at_start"] = rest_snapshot
    result["self"]["can_move"] = bool(
        own.get("alive", True)
        and rest_snapshot == 0
        and not own.get("battle_id")
        and pid not in state.get("locked_this_turn", [])
    )
    result["self"]["inventory"] = [
        deepcopy(obj)
        for obj in _items(state.get("objects", {}))
        if obj.get("location", {}).get("kind") == "carried"
        and obj["location"].get("holder_id") == pid
    ]
    if not own.get("alive", True):
        return result
    radius = 2 if own.get("type") == "knight" else 1
    position = own["position"]
    result["visible_cells"] = [
        {"row": row, "col": col}
        for row in range(
            max(0, position["row"] - radius),
            min(state["board"]["rows"], position["row"] + radius + 1),
        )
        for col in range(
            max(0, position["col"] - radius),
            min(state["board"]["cols"], position["col"] + radius + 1),
        )
    ]
    result["pieces"] = [
        _piece(p) for p in _items(pieces) if p.get("alive", True) and _in_view(own, p["position"])
    ]
    result["objects"] = [
        deepcopy(obj)
        for obj in _items(state.get("objects", {}))
        if obj.get("location", {}).get("kind") == "ground"
        and _in_view(own, obj["location"]["position"])
    ]
    result["tombstones"] = [
        {key: deepcopy(tomb[key]) for key in ("id", "piece_id", "position") if key in tomb}
        for tomb in _items(state.get("tombstones", {}))
        if _in_view(own, tomb["position"])
    ]
    return result


def project_event(state, event: dict, pid: str) -> dict | None:
    """Project at commit, then persist this receipt; never replay with new visibility."""
    state, event = _plain(state), _plain(event)
    kind = event.get("type", event.get("kind", ""))
    if kind in PRIVATE_EVENTS:
        # Reasoning stays in operation records, even for the owner; no receipts.
        return None
    pieces = state.get("pieces", {})
    own = (
        pieces.get(pid)
        if isinstance(pieces, dict)
        else next((p for p in pieces if p["id"] == pid), None)
    )
    if not own or not own.get("alive", True):
        return None
    payload = event.get("payload", {})
    audience = event.get("audience", payload.get("audience", []))
    admitted = isinstance(audience, list) and pid in audience
    if ("audience" in event or "audience" in payload) and not admitted:
        return None
    header = {key: deepcopy(event[key]) for key in EVENT_FIELDS if key in event}
    if kind in LANGUAGE_EVENTS:
        if not admitted:
            return None
        header["payload"] = {
            key: deepcopy(payload[key]) for key in LANGUAGE_FIELDS if key in payload
        }
        return header
    if kind in {"BattleNarrated", "BattleNarrative"}:
        if not admitted:
            return None
        header["payload"] = {
            key: deepcopy(payload[key])
            for key in ("battle_id", "round", "public_narrative")
            if key in payload
        }
        return header
    if kind in {"BattleNotice", "BattleStarted", "BattleJoined", "BattleMerged", "BattlesMerged"}:
        anchor = payload.get("anchor")
        if not admitted and not (
            isinstance(anchor, dict)
            and abs(own["position"]["row"] - anchor["row"]) <= 2
            and abs(own["position"]["col"] - anchor["col"]) <= 2
        ):
            return None
        header["payload"] = {
            key: deepcopy(payload[key])
            for key in (
                "battle_id",
                "anchor",
                "status",
                "participants",
                "participant_ids",
                "members",
                "piece_id",
                "parent_ids",
            )
            if key in payload
        }
        return header
    if kind in {"Death", "PieceDied", "DeathExecuted", "DeathCommitted"}:
        position = payload.get("position")
        if not admitted and not (isinstance(position, dict) and _in_view(own, position)):
            return None
        # A witness sees the execution; tombstone observation never receives killer.
        header["payload"] = {
            key: deepcopy(payload[key])
            for key in ("piece_id", "position", "killer_id", "executor_id", "object_ids")
            if key in payload
        }
        if "actor_id" in event:
            header["payload"]["executor_id"] = event["actor_id"]
        return header
    if kind in {"TurnStarted", "TurnCompleted"}:
        return {**header, "payload": {"turn": event.get("turn", payload.get("turn"))}}
    position = payload.get("position", payload.get("actual_to", payload.get("to")))
    if not admitted and not (isinstance(position, dict) and _in_view(own, position)):
        return None
    if (
        kind in {"ObjectPicked", "ObjectPickedUp", "ObjectDropped", "ObjectTransferred"}
        and payload.get("holder_id", payload.get("recipient_id")) != pid
    ):
        # Seeing an object vanish cannot reveal a holder's complete inventory.
        header["payload"] = {
            key: deepcopy(payload[key])
            for key in ("object_id", "position", "location")
            if key in payload and not (key == "location" and payload[key].get("kind") == "carried")
        }
    else:
        header["payload"] = {
            key: deepcopy(payload[key]) for key in STRUCTURE_FIELDS if key in payload
        }
    return header
