"""Fail-fast integrity checks run before every persistent state commit."""

from chessx.domain.errors import RuleError
from chessx.domain.profile import RULE_MANIFEST_HASH, frozen_rule_manifest, manifest_hash
from chessx.rules.actions import validate_battle_partition
from chessx.rules.geometry import inside_board


def require(condition: bool, message: str) -> None:
    if not condition:
        raise RuleError("INVARIANT_VIOLATION", message)


def assert_invariants(state: dict) -> None:
    pieces = state["pieces"]
    require(
        state["rule_version"] == "2.1" and state["rule_profile"] == "demo-v2.1",
        "Unsupported world rule version",
    )
    require(
        state.get("rule_manifest") == frozen_rule_manifest(),
        "World rule manifest differs from the frozen profile",
    )
    require(
        state.get("rule_manifest_hash") == RULE_MANIFEST_HASH
        and manifest_hash(state["rule_manifest"]) == RULE_MANIFEST_HASH,
        "World rule manifest hash is inconsistent",
    )
    for field in ("turn", "completed_turn", "seq", "generation"):
        require(type(state[field]) is int and state[field] >= 0, f"Invalid world counter: {field}")
    require(state["completed_turn"] <= state["turn"], "Completed turn cannot exceed current turn")
    require(len(pieces) <= 8, "A world may contain at most eight pieces")
    require(
        8 <= state["board"]["rows"] <= 64 and 8 <= state["board"]["cols"] <= 64,
        "Invalid board dimensions",
    )
    for pid, piece in pieces.items():
        require(piece["id"] == pid, "Piece ID and mapping key disagree")
        require(
            type(piece["rest_remaining"]) is int and piece["rest_remaining"] >= 0,
            "Rest must be a nonnegative integer",
        )
        require(inside_board(state, piece["position"]), "Piece position is outside the board")
        require(
            piece["type"] in {"king", "queen", "rook", "bishop", "knight", "pawn"},
            "Unknown piece type",
        )
        require(
            isinstance(piece["color"], str) and 1 <= len(piece["color"]) <= 32,
            "Invalid color label",
        )
        if not piece["alive"]:
            require(
                not piece["battle_id"] and not piece["conversation_id"],
                "Dead pieces cannot have active memberships",
            )
        cid = piece["conversation_id"]
        if cid is not None:
            conv = state["conversations"].get(cid)
            require(
                conv is not None and conv["status"] == "active" and pid in conv["members"],
                "Dangling conversation membership",
            )
        bid = piece["battle_id"]
        if bid is not None:
            battle = state["battles"].get(bid)
            require(
                battle is not None
                and battle["status"] in {"active", "settling"}
                and pid in battle["members"],
                "Dangling battle membership",
            )
            require(cid is None, "Battle participants cannot also belong to ordinary conversations")
    for oid, obj in state["objects"].items():
        require(obj["id"] == oid, "Object ID and mapping key disagree")
        loc = obj["location"]
        if loc.get("kind") == "ground":
            require(
                set(loc) == {"kind", "position"} and inside_board(state, loc["position"]),
                "Invalid ground object location",
            )
        else:
            require(
                loc.get("kind") == "carried" and set(loc) == {"kind", "holder_id"},
                "Object must have exactly one location",
            )
            holder = pieces.get(loc["holder_id"])
            require(holder is not None and holder["alive"], "Object holder must be alive")
    for cid, conv in state["conversations"].items():
        if conv["status"] != "active":
            continue
        require(
            len(conv["members"]) == len(set(conv["members"])) and len(conv["members"]) >= 2,
            "Active conversation must have distinct members",
        )
        for pid in conv["members"]:
            require(pid in pieces and pieces[pid]["alive"], "Conversation member must be alive")
            if conv.get("kind", "ordinary") == "ordinary":
                require(
                    pieces[pid]["conversation_id"] == cid, "Conversation membership is inconsistent"
                )
        count = (
            len(set(conv["counted_message_ids"]))
            if "counted_message_ids" in conv
            else conv.get("message_count", len(conv.get("messages", [])))
        )
        if "counted_message_ids" in conv:
            require(
                len(conv["counted_message_ids"]) == count,
                "A message identity cannot be counted twice",
            )
            require(
                conv.get("message_count", count) == count,
                "Conversation message count is inconsistent",
            )
        require(
            0 <= count <= state["limits"]["conversation_max_messages"],
            "Conversation exceeded its message budget",
        )
    seen_members = set()
    for bid, battle in state["battles"].items():
        require(inside_board(state, battle["anchor"]), "Invalid battle anchor")
        require(
            type(battle["round"]) is int and 0 <= battle["round"] <= battle["max_rounds"],
            "Invalid battle round",
        )
        if battle["status"] not in {"active", "settling"}:
            continue
        require(
            len(set(battle["members"])) == len(battle["members"]), "Battle members must be distinct"
        )
        for pid in battle["members"]:
            require(
                pid in pieces and pieces[pid]["alive"] and pieces[pid]["battle_id"] == bid,
                "Battle membership is inconsistent",
            )
            require(pid not in seen_members, "A piece cannot belong to multiple battles")
            seen_members.add(pid)
        if battle["status"] == "settling":
            participants = battle.get("combat_participants", battle["members"])
            validate_battle_partition(
                participants,
                battle.get("winner_ids", []),
                battle.get("loser_ids", []),
                battle.get("surrendered", []),
            )
            for loser_id, resolution in battle.get("settlements", {}).items():
                require(
                    loser_id in battle["loser_ids"] and resolution in {"released", "executed"},
                    "Invalid settlement resolution",
                )
                require(
                    pieces[loser_id]["battle_id"] is None, "Resolved losers must leave the battle"
                )
                require(
                    pieces[loser_id]["alive"] == (resolution == "released"),
                    "Settlement outcome disagrees with piece state",
                )
                if resolution == "released":
                    approvals = battle.get("release_approvals", {}).get(loser_id, [])
                    living_winners = {pid for pid in battle["winner_ids"] if pieces[pid]["alive"]}
                    require(
                        set(approvals) == living_winners and len(approvals) == len(living_winners),
                        "Released loser lacks unanimous winner authorization",
                    )
    for tomb in state["tombstones"].values():
        require(
            tomb["piece_id"] in pieces and not pieces[tomb["piece_id"]]["alive"],
            "Tombstone must refer to a dead piece",
        )
        require(inside_board(state, tomb["position"]), "Invalid tombstone position")
        require(
            not ({"killer", "killer_id", "actor_id"} & set(tomb)),
            "Tombstones may not expose a killer",
        )
