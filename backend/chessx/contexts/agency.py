"""Action affordances and conversational recall derived only from owner-visible data."""

from copy import deepcopy

from chessx.rules.geometry import can_threaten, inside_board


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


def navigation(observation: dict, receipts: list[dict], pid: str, stage: str) -> dict:
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
    known = {}
    for receipt in receipts:
        channel, event, payload = receipt_event(receipt)
        if channel not in {"MoveCommitted", "PieceMoved", "DeathCommitted"}:
            continue
        who = payload.get("piece_id", event.get("actor_id"))
        position = payload.get(
            "position", payload.get("actual_to", payload.get("last_visible_position"))
        )
        if (
            isinstance(who, str)
            and who != pid
            and isinstance(position, dict)
            and inside_board({"board": board}, position)
        ):
            known[who] = {
                "piece_id": who,
                "position": deepcopy(position),
                "current_visible": False,
                "last_seen_turn": event.get("turn"),
                "status_at_sighting": "dead" if channel == "DeathCommitted" else "alive",
            }
    for piece in observation["pieces"]:
        if piece["id"] != pid:
            known[piece["id"]] = {
                "piece_id": piece["id"],
                "name": piece["name"],
                "position": deepcopy(piece["position"]),
                "current_visible": True,
                "status_at_sighting": "alive",
            }
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
    return {
        "current_position": deepcopy(origin),
        "coordinate_system": "Zero-based [row,col]; rows increase downward, columns rightward. The whole board is a traversable space, not a meeting room.",
        "can_choose_move_in_this_stage": stage == "action" and own["can_move"],
        "can_move_at_primary_phase": own["can_move"],
        "rest_at_turn_start": own["rest_at_start"],
        "geometric_move_targets": targets,
        "target_format": "Each pair is [row,col]; emit move with target={row:R,col:C}. Targets are unranked, exclude waiting, and do NOT guarantee an unobstructed path.",
        "known_locations": list(known.values()),
        "unseen_locations": "Unknown. Use your own remembered reports cautiously or explore; nobody supplies hidden live coordinates or a route to an unseen person.",
        "conversation_blocks_movement": False,
        "movement_leaves_conversation": True,
        "recent_consecutive_waits": waits,
        "travel_cost": "Successful knight move rests 3 turns; other moves rest max(|delta_row|,|delta_col|). Short moves cost less rest. Obstacles are handled by contact negotiation.",
    }
