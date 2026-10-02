"""Separate least-privilege builders for characters, narrator and bard."""

from copy import deepcopy

from chessx.domain.scenarios import frozen_discipline

from .agency import dialogue_recall, navigation, pending_move_context
from .memory import NOISE, recall
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
    "latest_message",
    "conversation_version",
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
    observation = observe(state, pid)
    receipts = state.get("receipts", {}).get(pid, [])
    return {
        "discipline": discipline,
        "discipline_hash": discipline_hash,
        "context_channels": {
            "discipline": "common instructions below engine protocol",
            "persona": "owner-private immutable design, descriptive",
            "memory": "owner-private subjective summary",
            "evidence_index": "program-built receipt index, not beliefs or new world facts",
            "memory_recall": "owner-private attributed sources; claims are not verdicts",
            "goals": "public goals remain private until expressed",
            "observation": "current local rule facts",
            "receipts": "personally delivered events",
            "protocol": "authoritative stage permissions",
        },
        "rule_profile": state.get("rule_profile", "demo-v2.1"),
        "rules": {
            "limits": deepcopy(state.get("limits", {})),
            "movement": "king:8 neighbors; queen:orthogonal/diagonal; rook:orthogonal; bishop:diagonal; knight:(1,2)/(2,1) jump endpoints only; pawn:4 orthogonal neighbors, diagonal1 only onto living occupant, threats:4 diagonals. No promotion.",
            "rest": "Actual move rests max(|dr|,|dc|); knight success3, blocked0. Turn-start rest blocks move/invite all turn; stationary actions allowed.",
            "interactions": "Occupied routes require contact negotiation. Endpoint co-location:equal. Equal/neither-threat requires agreement; unilateral advantage decides passage. Max50 messages; no eavesdropping.",
            "combat": "Failed negotiation may battle. Join only by arriving at active anchor; broadcast is information. Everyone wins/loses, no neutral. Any winner may execute; release unanimous. No attack/HP/dice/color strength.",
            "objects": "No creation/destruction/auto-pickup. Ground locally visible; inventories owner-only. Transfer can be rejected; original holder retained.",
        },
        "turn": state.get("turn", 0),
        "stage": stage,
        "actor_id": pid,
        "persona": private.get("persona", ""),
        "memory": private.get("memory", "")
        if private.get("compaction_provenance") == "extractive_receipts_v1"
        else private.get("rolling_summary") or private.get("memory", ""),
        **(
            {
                "evidence_index": {
                    "kind": "extractive_receipts_v1",
                    "summary_lines": private.get("rolling_summary", "").splitlines(),
                    "scope": "Verbatim delivered evidence and pointers; no model reflection or truth certification.",
                }
            }
            if private.get("compaction_provenance") == "extractive_receipts_v1"
            else {}
        ),
        "goals": private.get("goals", ""),
        "receipts": deepcopy(
            [
                r
                for r in receipts[private.get("compaction_watermark", 0) :]
                if r.get("channel") not in NOISE
            ][-30:]
        ),
        "observation": observation,
        **(
            {
                "navigation": {
                    **navigation(
                        observation,
                        receipts,
                        pid,
                        stage,
                        turn=state.get("turn", 0),
                        spatial_memory=private.get("spatial_memory"),
                    ),
                    **pending_move_context(
                        observation, pid, stage, protocol, state.get("cursor", {}).get("work")
                    ),
                },
                "memory_recall": recall(state, pid, protocol),
            }
            if stage
            in {
                "action",
                "ordinary",
                "negotiation",
                "settlement",
                "invitation",
                "invite_response",
                "accept_transfer",
                "surrender",
                "memory",
            }
            else {}
        ),
        **(
            {"dialogue_recall": dialogue_recall(receipts, pid, protocol.get("conversation_id"))}
            if stage in {"action", "ordinary", "negotiation", "settlement"}
            else {}
        ),
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
        "MemoryCompactionAdvanced",
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
        "StoryResumeRequested",
        "ModelCapabilityVerified",
        "NegotiationPermissionUsed",
        "SettlementPermissionPassed",
    }
    public_keys = {
        "piece_id",
        "actor_id",
        "speaker_id",
        "speaker_name",
        "piece_name",
        "sender_id",
        "recipient_id",
        "conversation_id",
        "battle_id",
        "speech",
        "text",
        "public_narrative",
        "from",
        "origin",
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
            for key in ("id", "event_id", "seq", "turn", "type", "kind", "actor_id")
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
        "public_cast": [
            {"id": pid, "name": piece.get("name", pid)}
            for pid, piece in state.get("pieces", {}).items()
            if any(
                pid == event.get("actor_id")
                or pid
                in (
                    event.get("payload", {}).get("speaker_id"),
                    event.get("payload", {}).get("piece_id"),
                    event.get("payload", {}).get("sender_id"),
                    event.get("payload", {}).get("recipient_id"),
                )
                for event in allowed
            )
        ],
        "instruction": "Select and adapt meaningful events into a natural story, not a turn-by-turn report. Merge repetition and omit low-signal acts; preserve causal outcomes. Speech is its speaker's claim, not proof. Cite only original IDs supporting each paragraph's core facts.",
    }
