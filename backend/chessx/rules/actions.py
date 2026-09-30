"""Atomic rule transitions. The caller persists state and returned event drafts."""

from copy import deepcopy
from uuid import uuid4

from pydantic import TypeAdapter

from chessx.domain.errors import RuleError
from chessx.domain.models import Intent
from chessx.rules.geometry import inside_board, live_piece, visible_positions

INTENT_ADAPTER = TypeAdapter(Intent)


def event(kind: str, actor: str | None, payload: dict, audience: list[str] | None = None) -> dict:
    result = {"type": kind, "actor_id": actor, "payload": payload}
    if audience is not None:
        result["audience"] = audience
    return result


def primary_piece(state: dict, pid: str) -> dict:
    piece = live_piece(state, pid)
    if piece["battle_id"] or pid in state.get("locked_this_turn", []):
        raise RuleError("BATTLE_LOCKED", "Battle participants cannot take a primary action")
    return piece


def owned_object(state: dict, pid: str, object_id: str) -> dict:
    obj = state["objects"].get(object_id)
    if obj is None or obj["location"] != {"kind": "carried", "holder_id": pid}:
        raise RuleError("OBJECT_NOT_HELD", "The requested object is not held by this piece")
    return obj


def leave_conversation(state: dict, pid: str) -> list[dict]:
    piece = state["pieces"][pid]
    cid = piece.get("conversation_id")
    if cid is None:
        return []
    conv = state["conversations"].get(cid)
    piece["conversation_id"] = None
    if conv is None:
        return []
    audience = list(conv["members"])
    conv["members"] = [member for member in conv["members"] if member != pid]
    # Every membership change invalidates the voter snapshot.
    conv["pending_proposal"] = None
    drafts = [
        event(
            "MembershipChanged",
            pid,
            {"conversation_id": cid, "piece_id": pid, "change": "left"},
            audience,
        )
    ]
    if len(conv["members"]) < 2:
        for member in conv["members"]:
            state["pieces"][member]["conversation_id"] = None
        conv["status"] = "closed"
        conv["members"] = []
        drafts.append(
            event(
                "ConversationClosed",
                pid,
                {"conversation_id": cid, "reason": "insufficient_members"},
                audience,
            )
        )
    return drafts


def apply_primary(state: dict, pid: str, intent: dict | object) -> list[dict]:
    parsed = INTENT_ADAPTER.validate_python(intent)
    data = parsed.model_dump(exclude_none=True)
    piece = primary_piece(state, pid)
    return _apply_unlocked(state, pid, piece, data)


def apply_settlement_action(
    state: dict, battle_id: str, pid: str, intent: dict | object
) -> list[dict]:
    """Authorize a narrow, voluntary settlement operation by its own actor."""
    parsed = INTENT_ADAPTER.validate_python(intent)
    data = parsed.model_dump(exclude_none=True)
    piece = live_piece(state, pid)
    battle = state["battles"].get(battle_id)
    if battle is None or battle["status"] != "settling" or pid not in battle["members"]:
        raise RuleError(
            "SETTLEMENT_UNAUTHORIZED", "Only participants in an active settlement may act"
        )
    if piece["battle_id"] != battle_id or data["kind"] not in {"drop", "transfer", "change_color"}:
        raise RuleError(
            "SETTLEMENT_ACTION_INVALID", "This action is not authorized during settlement"
        )
    return _apply_unlocked(state, pid, piece, data)


def _apply_unlocked(state: dict, pid: str, piece: dict, data: dict) -> list[dict]:
    kind = data["kind"]
    if kind == "wait":
        return [event("PieceWaited", pid, {"piece_id": pid})]
    if kind == "change_color":
        old = piece["color"]
        piece["color"] = data["label"]
        return [
            event("ColorChanged", pid, {"piece_id": pid, "old_color": old, "color": data["label"]})
        ]
    if kind == "pickup":
        obj = state["objects"].get(data["object_id"])
        if obj is None or obj["location"] != {"kind": "ground", "position": piece["position"]}:
            raise RuleError(
                "OBJECT_NOT_AT_POSITION",
                "The requested ground object is not at this piece's position",
            )
        obj["location"] = {"kind": "carried", "holder_id": pid}
        return [
            event(
                "ObjectPicked",
                pid,
                {"object_id": obj["id"], "holder_id": pid, "position": deepcopy(piece["position"])},
            )
        ]
    if kind == "drop":
        obj = owned_object(state, pid, data["object_id"])
        obj["location"] = {"kind": "ground", "position": deepcopy(piece["position"])}
        return [
            event(
                "ObjectDropped",
                pid,
                {"object_id": obj["id"], "position": deepcopy(piece["position"])},
            )
        ]
    if kind == "transfer":
        offer = create_transfer_offer(state, pid, data["object_id"], data["target_id"])
        return [
            event(
                "ObjectTransferOffered",
                pid,
                {
                    "offer_id": offer["id"],
                    "object_id": offer["object_id"],
                    "sender_id": pid,
                    "recipient_id": offer["recipient_id"],
                },
                [pid, offer["recipient_id"]],
            )
        ]
    if kind == "join_battle":
        return authorize_join(state, pid, data["battle_id"])
    raise RuleError(
        "ACTION_REQUIRES_PROTOCOL", "This action requires its own movement or conversation protocol"
    )


def create_transfer_offer(state: dict, pid: str, object_id: str, target_id: str) -> dict:
    sender = live_piece(state, pid)
    target = live_piece(state, target_id)
    if pid == target_id:
        raise RuleError("TRANSFER_SELF", "A transfer must target another piece")
    obj = owned_object(state, pid, object_id)
    if target["position"] not in visible_positions(state, pid):
        raise RuleError("TARGET_NOT_VISIBLE", "The transfer target must be visible")
    offers = state.setdefault("transfer_offers", {})
    if any(o["status"] == "pending" and o["object_id"] == object_id for o in offers.values()):
        raise RuleError("TRANSFER_ALREADY_PENDING", "This object already has a pending transfer")
    offer = {
        "id": f"offer_{uuid4().hex}",
        "object_id": object_id,
        "sender_id": pid,
        "recipient_id": target_id,
        "origin_slot": state.get("cursor", {}).get("slot", f"turn:{state['turn']}:primary:{pid}"),
        "read_set": {
            "location": deepcopy(obj["location"]),
            "sender_position": deepcopy(sender["position"]),
            "recipient_position": deepcopy(target["position"]),
        },
        "status": "pending",
    }
    offers[offer["id"]] = offer
    return offer


def resolve_transfer(state: dict, offer: dict | str, accept: bool) -> list[dict]:
    if type(accept) is not bool:
        raise RuleError("TRANSFER_RESPONSE_INVALID", "A transfer response must be accept or reject")
    oid = offer if isinstance(offer, str) else offer["id"]
    stored = state.get("transfer_offers", {}).get(oid)
    if stored is None:
        raise RuleError("TRANSFER_OFFER_UNKNOWN", "The transfer offer is unavailable")
    if stored["status"] != "pending":
        return []
    sender_id, recipient_id = stored["sender_id"], stored["recipient_id"]
    audience = [sender_id, recipient_id]
    payload = {
        "offer_id": oid,
        "object_id": stored["object_id"],
        "sender_id": sender_id,
        "recipient_id": recipient_id,
    }
    sender = state["pieces"].get(sender_id)
    recipient = state["pieces"].get(recipient_id)
    obj = state["objects"].get(stored["object_id"])
    valid = (
        sender is not None
        and sender["alive"]
        and recipient is not None
        and recipient["alive"]
        and obj is not None
        and obj["location"] == {"kind": "carried", "holder_id": sender_id}
        and recipient["position"] in visible_positions(state, sender_id)
    )
    if not valid:
        stored["status"] = "cancelled"
        return [
            event(
                "OfferCancelled",
                recipient_id,
                {**payload, "reason": "precondition_changed"},
                audience,
            )
        ]
    if not accept:
        stored["status"] = "rejected"
        return [event("ObjectTransferRejected", recipient_id, payload, audience)]
    stored["status"] = "accepted"
    obj["location"] = {"kind": "carried", "holder_id": recipient_id}
    return [event("ObjectTransferred", recipient_id, payload, audience)]


def authorize_join(state: dict, pid: str, battle_id: str) -> list[dict]:
    piece = live_piece(state, pid)
    battle = state["battles"].get(battle_id)
    if battle is None or battle["status"] != "active":
        raise RuleError("BATTLE_NOT_ACTIVE", "Only active battles may be joined")
    if piece["battle_id"] == battle_id and pid in battle["members"]:
        return []
    if piece["battle_id"] is not None:
        raise RuleError("BATTLE_ALREADY_ACTIVE", "This piece already belongs to a battle")
    if pid in state.get("locked_this_turn", []):
        raise RuleError("BATTLE_TURN_LOCKED", "This piece cannot take another action this turn")
    if piece["position"] != battle["anchor"]:
        raise RuleError(
            "BATTLE_NOT_AT_ANCHOR", "Joining requires physical presence at the battle anchor"
        )
    drafts = leave_conversation(state, pid)
    if pid not in battle["members"]:
        battle["members"].append(pid)
    piece["battle_id"] = battle_id
    locks = state.setdefault("locked_this_turn", [])
    if pid not in locks:
        locks.append(pid)
    drafts.append(
        event(
            "BattleJoined",
            pid,
            {"battle_id": battle_id, "piece_id": pid, "anchor": deepcopy(battle["anchor"])},
            list(battle["members"]),
        )
    )
    return drafts


def validate_battle_partition(
    members: list[str],
    winner_ids: list[str],
    loser_ids: list[str],
    surrendered: list[str] | None = None,
) -> None:
    winners, losers = set(winner_ids), set(loser_ids)
    if (
        not winners
        or not losers
        or len(winners) != len(winner_ids)
        or len(losers) != len(loser_ids)
        or winners & losers
        or winners | losers != set(members)
        or not set(surrendered or []).issubset(losers)
    ):
        raise RuleError(
            "BATTLE_PARTITION_INVALID",
            "A battle requires complete, disjoint, nonempty winner and loser sets; surrendering pieces must lose",
        )


def settlement_authority(state: dict, battle_id: str, winner_id: str, loser_id: str) -> dict:
    battle = state["battles"].get(battle_id)
    if battle is None or battle["status"] != "settling":
        raise RuleError("SETTLEMENT_NOT_ACTIVE", "This battle is not in settlement")
    live_piece(state, winner_id)
    live_piece(state, loser_id)
    if winner_id not in battle.get("winner_ids", []) or loser_id not in battle.get("loser_ids", []):
        raise RuleError(
            "SETTLEMENT_UNAUTHORIZED", "Only a battle winner may dispose of a battle loser"
        )
    if battle.get("current_loser_id", loser_id) != loser_id:
        raise RuleError(
            "SETTLEMENT_WRONG_LOSER", "This loser is not the current settlement subject"
        )
    if loser_id in battle.get("settlements", {}):
        raise RuleError("SETTLEMENT_ALREADY_RESOLVED", "This loser has already been disposed of")
    if state["pieces"][loser_id].get("battle_id") != battle_id:
        raise RuleError("SETTLEMENT_UNAUTHORIZED", "The loser no longer belongs to this battle")
    return battle


def kill_loser(state: dict, battle_id: str, winner_id: str, loser_id: str) -> list[dict]:
    battle = state["battles"].get(battle_id)
    if battle and battle.get("settlements", {}).get(loser_id) == "executed":
        return []
    battle = settlement_authority(state, battle_id, winner_id, loser_id)
    loser = state["pieces"][loser_id]
    drafts = leave_conversation(state, loser_id)
    position = deepcopy(loser["position"])
    loser["alive"] = False
    loser["battle_id"] = None
    battle.setdefault("combat_participants", list(battle["members"]))
    battle["members"] = [pid for pid in battle["members"] if pid != loser_id]
    battle.setdefault("settlements", {})[loser_id] = "executed"
    tombstone_id = f"tomb_{uuid4().hex}"
    state["tombstones"][tombstone_id] = {
        "id": tombstone_id,
        "piece_id": loser_id,
        "position": position,
        "death_event_id": None,  # The event store binds the committed event identity.
    }
    drafts.append(
        event(
            "DeathCommitted",
            winner_id,
            {
                "piece_id": loser_id,
                "position": position,
                "tombstone_id": tombstone_id,
                "battle_id": battle_id,
            },
        )
    )
    for obj in state["objects"].values():
        if obj["location"] == {"kind": "carried", "holder_id": loser_id}:
            obj["location"] = {"kind": "ground", "position": deepcopy(position)}
            drafts.append(
                event(
                    "ObjectDroppedByDeath", loser_id, {"object_id": obj["id"], "position": position}
                )
            )
    for offer in state.get("transfer_offers", {}).values():
        if offer["status"] == "pending" and loser_id in {offer["sender_id"], offer["recipient_id"]}:
            offer["status"] = "cancelled"
            drafts.append(
                event(
                    "OfferCancelled",
                    loser_id,
                    {"offer_id": offer["id"], "reason": "piece_died"},
                    [offer["sender_id"], offer["recipient_id"]],
                )
            )
    drafts.append(
        event(
            "MembershipChanged",
            loser_id,
            {"battle_id": battle_id, "piece_id": loser_id, "change": "died"},
        )
    )
    return drafts


def release_loser(
    state: dict, battle_id: str, winner_id: str, loser_id: str, approvals: dict[str, bool]
) -> list[dict]:
    battle = state["battles"].get(battle_id)
    if battle and battle.get("settlements", {}).get(loser_id) == "released":
        return []
    battle = settlement_authority(state, battle_id, winner_id, loser_id)
    winners = {pid for pid in battle["winner_ids"] if state["pieces"][pid]["alive"]}
    if (
        not winners
        or set(approvals) != winners
        or not all(value is True for value in approvals.values())
    ):
        raise RuleError(
            "RELEASE_NOT_UNANIMOUS", "Release requires explicit approval from every living winner"
        )
    loser = state["pieces"][loser_id]
    loser["battle_id"] = None
    battle.setdefault("combat_participants", list(battle["members"]))
    battle["members"] = [pid for pid in battle["members"] if pid != loser_id]
    battle.setdefault("settlements", {})[loser_id] = "released"
    battle.setdefault("release_approvals", {})[loser_id] = sorted(winners)
    locks = state.setdefault("locked_this_turn", [])
    if loser_id not in locks:
        locks.append(loser_id)
    return [
        event(
            "LoserReleased",
            winner_id,
            {"battle_id": battle_id, "piece_id": loser_id, "approvals": sorted(winners)},
            list(winners | {loser_id}),
        )
    ]


def merge_battles(state: dict, ids: list[str], anchor: dict) -> tuple[str, list[dict]]:
    if len(ids) < 2 or len(set(ids)) != len(ids):
        raise RuleError(
            "BATTLE_MERGE_INVALID", "Merging requires at least two distinct active battles"
        )
    if not inside_board(state, anchor):
        raise RuleError("BATTLE_MERGE_INVALID", "The merge anchor is outside the board")
    parents = [state["battles"].get(bid) for bid in ids]
    if any(
        parent is None or parent["status"] != "active" or parent["anchor"] != anchor
        for parent in parents
    ):
        raise RuleError(
            "BATTLE_MERGE_INVALID", "Only active battles at the same physical anchor may merge"
        )
    members = []
    for parent in parents:
        for pid in parent["members"]:
            piece = live_piece(state, pid)
            if piece["battle_id"] != parent["id"] or pid in members:
                raise RuleError("BATTLE_MERGE_INVALID", "Parent membership is inconsistent")
            members.append(pid)
    new_id = f"battle_{uuid4().hex}"
    new_battle = {
        "id": new_id,
        "status": "active",
        "anchor": deepcopy(anchor),
        "members": members,
        "parent_ids": list(ids),
        "created_turn": state["turn"],
        "round": 0,
        "max_rounds": state["limits"]["battle_max_rounds"],
        "narratives": [],
        "parent_history": [
            {
                "battle_id": p["id"],
                "narratives": deepcopy(p.get("narratives", [])),
                "round": p["round"],
                "max_rounds": p["max_rounds"],
            }
            for p in parents
        ],
    }
    for parent in parents:
        parent["status"] = "merged"
        parent["merged_into"] = new_id
    for pid in members:
        state["pieces"][pid]["battle_id"] = new_id
    state["battles"][new_id] = new_battle
    return new_id, [
        event(
            "BattlesMerged",
            None,
            {
                "battle_id": new_id,
                "parent_ids": list(ids),
                "anchor": deepcopy(anchor),
                "members": members,
                "round": 0,
                "max_rounds": new_battle["max_rounds"],
            },
            members,
        )
    ]
