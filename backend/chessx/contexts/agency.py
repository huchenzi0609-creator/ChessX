"""Action affordances and conversational recall derived only from owner-visible data."""

from copy import deepcopy

from chessx.rules.geometry import can_threaten

from .spatial import (
    approach_options,
    movement_options,
    spatial_recall,
)
from .spatial import (
    pending_move_context as pending_move_context,
)


def receipt_event(receipt: dict) -> tuple[str, dict, dict]:
    event = receipt.get("payload", {})
    if not isinstance(event, dict):
        return "", {}, {}
    payload = event.get("payload", event)
    return (
        receipt.get("channel", event.get("type", "")),
        event,
        payload if isinstance(payload, dict) else {},
    )


def dialogue_recall(receipts: list[dict], pid: str, conversation_id: str | None = None) -> dict:
    heard = []
    for receipt in receipts:
        channel, event, payload = receipt_event(receipt)
        if channel not in {"ConversationMessage", "NegotiationMessage", "SettlementMessage"}:
            continue
        if conversation_id and payload.get("conversation_id") != conversation_id:
            continue
        speech = payload.get("speech")
        if not isinstance(speech, str):
            continue
        heard.append(
            {
                "speaker_id": payload.get("speaker_id", event.get("actor_id")),
                "speech": speech,
                "message_id": payload.get("message_id", payload.get("id")),
            }
        )
    return {
        "recent_heard": deepcopy(heard[-12:]),
        "latest_heard": deepcopy(heard[-1]) if heard else None,
        "last_own_utterances": [item["speech"] for item in heard if item["speaker_id"] == pid][-3:],
        "scope": "Only personally delivered speech; a bounded recent window, not the whole conversation.",
    }


def navigation(
    observation: dict,
    receipts: list[dict],
    pid: str,
    stage: str,
    *,
    turn: int | None = None,
    spatial_memory: dict | None = None,
    rules_state: dict | None = None,
) -> dict:
    own, board = observation["self"], observation["board"]
    origin = own["position"]
    # Geometry may cover unexplored cells. Occupancy is deliberately NOT read
    # from the global world: unseen blockers are encountered by movement later.
    targets = []
    if own["can_move"]:
        occupied = {
            (p["position"]["row"], p["position"]["col"])
            for p in observation["pieces"]
            if p["id"] != pid
        }
        for row in range(board["rows"]):
            for col in range(board["cols"]):
                target = {"row": row, "col": col}
                if target == origin:
                    continue
                dr, dc = abs(row - origin["row"]), abs(col - origin["col"])
                permitted = (
                    dr + dc == 1 or dr == dc == 1 and (row, col) in occupied
                    if own["type"] == "pawn"
                    else can_threaten(own, target)
                )
                if permitted:
                    targets.append([row, col])
    waits = 0
    for receipt in reversed(receipts):
        channel, event, payload = receipt_event(receipt)
        actor = payload.get("piece_id", event.get("actor_id"))
        if actor != pid or channel not in {
            "PieceWaited",
            "MoveCommitted",
            "ObjectPickedUp",
            "ObjectDropped",
            "ColorChanged",
            "ConversationCreated",
        }:
            continue
        if channel != "PieceWaited":
            break
        waits += 1
    spatial = spatial_recall(observation, receipts, pid, turn=turn, spatial_memory=spatial_memory)
    return {
        "current_position": deepcopy(origin),
        "coordinate_system": "Zero-based [row,col]. 北=north=row decreases; 南=south=row increases; 东=east=col increases; 西=west=col decreases. The board is a traversable space.",
        "distance_semantics": "distance_steps=max(|dr|,|dc|), not route time. Bearings use your current position; old sightings may be stale.",
        "can_choose_move_in_this_stage": stage == "action" and own["can_move"],
        "can_move_at_primary_phase": own["can_move"],
        "rest_at_turn_start": own["rest_at_start"],
        "geometric_move_targets": targets,
        "target_format": "Each pair is [row,col]; emit move with target={row:R,col:C}. Targets are unranked, exclude waiting, and do NOT guarantee an unobstructed path.",
        "movement_options": movement_options(observation, targets, rules_state=rules_state),
        "movement_options_scope": "Up to 12 examples; complete targets above. Contacts require negotiation; unknown cells may be occupied. Choose by your own purpose.",
        **spatial,
        "toward_known_people": approach_options(
            observation, targets, spatial["known_locations"], rules_state=rules_state
        ),
        "toward_known_people_scope": "Optional legal next steps toward YOUR remembered place, not mandatory targets or live tracking. distance_before/after are geometric. already_visible people may be invited without moving into their occupied cell. A past sighting may now be empty; unknown path cells are not guaranteed free.",
        "unseen_locations": "last_seen is your dated sighting, not live tracking. Reports are unverified; unseen current positions are unknown.",
        "conversation_blocks_movement": False,
        "movement_leaves_conversation": True,
        "recent_consecutive_waits": waits,
        "travel_cost": "Actual travel rests max(|delta_row|,|delta_col|); blocked moves rest0. Consult rest_turns_if_success for the frozen world policy. Obstacles are handled by contact negotiation.",
    }
