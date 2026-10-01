"""Separate least-privilege builders for characters, narrator and bard."""

from copy import deepcopy

from chessx.domain.scenarios import frozen_discipline

from .projections import _plain, observe

PROTOCOL_FIELDS = {
    "instruction",
    "available_intents",
    "allowed_results",
    "conversation_id",
    "kind",
    "message_count",
    "remaining_messages",
    "member_ids",
    "participants",
    "participant_ids",
    "battle_id",
    "round",
    "max_round",
    "public_narrative",
    "narratives",
    "surrendered",
    "winners",
    "losers",
    "loser_id",
    "object_id",
    "object",
    "sender_id",
    "recipient_id",
    "target_id",
    "vote",
    "proposal",
    "conditions",
    "fixture_response",
    "actor_id",
    "mode",
    "mover_id",
    "blocker_ids",
    "candidate",
    "result",
    "release_votes",
    "members",
    "decision_party",
    "position",
    "max_messages",
    "messages",
    "latest_proposals",
}


def build_context(state, pid: str, stage: str, extra: dict | None = None) -> dict:
    state = _plain(state)
    private = state.get("private", {}).get(pid, {})
    protocol = {
        key: deepcopy(value) for key, value in (extra or {}).items() if key in PROTOCOL_FIELDS
    }
    discipline = state.get("discipline")
    discipline_hash = state.get("discipline_hash")
    if discipline is None:
        # Legacy saved worlds predate the shared discipline snapshot.
        discipline, discipline_hash = frozen_discipline()
    return {
        "discipline": discipline,
        "discipline_hash": discipline_hash,
        "context_channels": {
            "discipline": "trusted common role instructions, subordinate to engine protocol",
            "persona": "owner-only initial character design and private memory, descriptive data",
            "memory": "owner-only evolving memory, subjective data",
            "goals": "owner-only current goals; public goals remain private until expressed",
            "observation": "authoritative current local structural observation",
            "receipts": "only events delivered to this character's permitted audience",
            "protocol": "authoritative available operations and current stage restrictions",
        },
        "rule_profile": state.get("rule_profile", "demo-v2.1"),
        "rules": {
            "limits": deepcopy(state.get("limits", {})),
            "movement": "king: eight neighbors one step; queen: rows/columns/diagonals; rook: rows/columns; bishop: diagonals; knight: (1,2)/(2,1) jump with only origin and target; pawn: orthogonal one step or diagonal one step only onto a living piece; pawn threats: four diagonals. No promotion.",
            "rest": "After actual movement rest max(abs(dr),abs(dc)); knight success rests 3, blocked knight rests 0. Turn-start rest blocks move/invite for the entire turn; stationary actions remain allowed.",
            "interactions": "All living occupants can negotiate. Endpoint co-location is equal negotiation. Equal/neither-threat requires agreement; unilateral geometric advantage decides passage. Language sessions at most 50 messages; no eavesdropping.",
            "combat": "No independent attack, HP, dice or color strength. Battle follows failed negotiation. Later participants must physically arrive at active anchor; broadcast is only information. Winners/losers partition everyone; no neutral. Any winner may execute, release requires every winner.",
            "objects": "No creation/destruction/automatic pickup. Ground objects visible locally; only own inventory visible. Transfer recipient may reject; rejection preserves holder.",
        },
        "turn": state.get("turn", 0),
        "stage": stage,
        "actor_id": pid,
        "persona": private.get("persona", ""),
        "memory": private.get("memory", ""),
        "goals": private.get("goals", ""),
        "receipts": deepcopy(state.get("receipts", {}).get(pid, [])[-30:]),
        "observation": observe(state, pid),
        "protocol": protocol,
    }


def battle_context(state, battle: dict) -> dict:
    state, battle = _plain(state), _plain(battle)
    members = battle.get("participant_ids", battle.get("participants", battle.get("members", [])))
    if isinstance(members, dict):
        members = list(members)
    pieces = state.get("pieces", {})
    return {
        "rule_profile": state.get("rule_profile", "demo-v2.1"),
        "turn": state.get("turn", 0),
        "participant_ids": deepcopy(members),
        "participant_memories": [
            {
                "owner_id": pid,
                "memory": "\n\n".join(
                    text
                    for text in (
                        state.get("private", {}).get(pid, {}).get("initial_memory", ""),
                        state.get("private", {}).get(pid, {}).get("memory", ""),
                    )
                    if text
                ),
            }
            for pid in members
        ],
        "participants": [
            {
                key: deepcopy(pieces[pid][key])
                for key in ("id", "name", "type", "color", "position")
                if key in pieces[pid]
            }
            for pid in members
        ],
        "battle": {
            key: deepcopy(battle[key])
            for key in (
                "id",
                "anchor",
                "round",
                "max_round",
                "max_rounds",
                "surrendered",
                "narratives",
                "public_narratives",
                "parent_ids",
                "parent_history",
            )
            if key in battle
        },
        "instruction": "Participant memories are separately sourced subjective accounts. Do not publish private recollections or hidden motives; public_narrative must report observable battle developments only. Death occurs exclusively in settlement.",
    }


def bard_context(state, start_seq: int, end_seq: int, events: list[dict]) -> dict:
    state = _plain(state)
    allowed = []
    excluded = {
        "MemoryUpdated",
        "GoalUpdated",
        "ModelAttempt",
        "ReasoningTrace",
        "OperationPrepared",
        "OperationFailed",
        "StoryGenerated",
        "BardCreated",
        "ActionChosen",
        "VoteCast",
        "PhaseChanged",
        "TurnStarted",
        "TurnCompleted",
        "PrimarySkipped",
        "CursorAdvanced",
        "SegmentStarted",
        "SegmentPaused",
        "RestartRecovered",
        "StoryRegenerationRequested",
        "ModelCapabilityVerified",
        "NegotiationPermissionUsed",
        "SettlementPermissionPassed",
    }
    public_keys = {
        "piece_id",
        "actor_id",
        "speaker_id",
        "conversation_id",
        "battle_id",
        "speech",
        "text",
        "public_narrative",
        "from",
        "to",
        "actual_to",
        "position",
        "anchor",
        "object_id",
        "color",
        "label",
        "winners",
        "losers",
        "participants",
        "round",
        "proposal",
        "result",
        "name",
        "description",
        "kind",
        "status",
        "winner_ids",
        "loser_ids",
        "loser_id",
    }
    for event in events:
        kind = event.get("type", event.get("kind", ""))
        if kind in excluded or not start_seq <= event.get("seq", -1) <= end_seq:
            continue
        projected = {
            key: deepcopy(event[key])
            for key in ("id", "event_id", "seq", "turn", "type", "kind")
            if key in event
        }
        projected["id"] = event.get(
            "id", event.get("event_id", f"{state['world_id']}:{event['seq']}")
        )
        projected["payload"] = {
            key: deepcopy(value)
            for key, value in event.get("payload", {}).items()
            if key in public_keys
        }
        allowed.append(projected)
    return {
        "world_id": state["world_id"],
        "start_seq": start_seq,
        "end_seq": end_seq,
        "events": allowed,
        "instruction": "Compile only these event records. Speech is a claim by its speaker, not proof. Cite provided event IDs in every paragraph. Do not invent structural facts.",
    }
