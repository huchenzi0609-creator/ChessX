"""Geometry, occupancy and turn-based rest. Color never affects geometry."""

from chessx.domain.errors import RuleError


def live_piece(state: dict, piece_id: str) -> dict:
    piece = state["pieces"].get(piece_id)
    if piece is None or not piece["alive"]:
        raise RuleError("PIECE_UNAVAILABLE", "The acting piece is unavailable")
    return piece


def inside_board(state: dict, position: dict) -> bool:
    return (
        type(position.get("row")) is int
        and type(position.get("col")) is int
        and 0 <= position["row"] < state["board"]["rows"]
        and 0 <= position["col"] < state["board"]["cols"]
    )


def delta(piece: dict, target: dict) -> tuple[int, int]:
    return target["row"] - piece["position"]["row"], target["col"] - piece["position"]["col"]


def can_threaten(piece: dict, target: dict) -> bool:
    dr, dc = delta(piece, target)
    ar, ac = abs(dr), abs(dc)
    if ar == ac == 0:
        return False
    match piece["type"]:
        case "king":
            return max(ar, ac) == 1
        case "pawn":
            return ar == ac == 1
        case "knight":
            return (ar, ac) in {(1, 2), (2, 1)}
        case "rook":
            return dr == 0 or dc == 0
        case "bishop":
            return ar == ac
        case "queen":
            return dr == 0 or dc == 0 or ar == ac
        case _:
            return False


def occupants(state: dict, pos: dict, exclude: str | None = None) -> list[str]:
    return sorted(
        pid
        for pid, piece in state["pieces"].items()
        if pid != exclude and piece["alive"] and piece["position"] == pos
    )


def rest_at_start(state: dict, piece_id: str) -> int:
    return state.get("rest_at_start", {}).get(piece_id, state["pieces"][piece_id]["rest_remaining"])


def legal_move(state: dict, pid: str, target: dict) -> None:
    piece = live_piece(state, pid)
    if piece["battle_id"] or pid in state.get("locked_this_turn", []):
        raise RuleError("BATTLE_LOCKED", "Battle participants cannot move")
    if rest_at_start(state, pid) > 0:
        raise RuleError("PIECE_RESTING", "This piece is resting for the current turn")
    if not inside_board(state, target):
        raise RuleError("MOVE_OUT_OF_BOUNDS", "The target is outside the board")
    dr, dc = delta(piece, target)
    if dr == dc == 0:
        return
    if piece["type"] == "pawn":
        valid = abs(dr) + abs(dc) == 1 or (
            abs(dr) == abs(dc) == 1 and bool(occupants(state, target, exclude=pid))
        )
    else:
        valid = can_threaten(piece, target)
    if not valid:
        raise RuleError("MOVE_GEOMETRY_INVALID", "The move is not legal for this piece type")


def trace_path(piece: dict, target: dict) -> list[dict]:
    origin = dict(piece["position"])
    dr, dc = delta(piece, target)
    if dr == dc == 0:
        return [origin]
    if piece["type"] in {"knight", "king", "pawn"}:
        return [origin, dict(target)]
    # Reject malformed direct calls instead of producing a path that misses target.
    if not can_threaten(piece, target):
        raise RuleError("MOVE_GEOMETRY_INVALID", "Cannot trace an illegal movement")
    row_step = (dr > 0) - (dr < 0)
    col_step = (dc > 0) - (dc < 0)
    return [
        {"row": origin["row"] + i * row_step, "col": origin["col"] + i * col_step}
        for i in range(max(abs(dr), abs(dc)) + 1)
    ]


def movement_rest(piece: dict, origin: dict, dest: dict) -> int:
    distance = max(abs(dest["row"] - origin["row"]), abs(dest["col"] - origin["col"]))
    return 0 if distance == 0 else 3 if piece["type"] == "knight" else distance


def visible_positions(state: dict, pid: str) -> list[dict]:
    piece = live_piece(state, pid)
    radius = 2 if piece["type"] == "knight" else 1
    row, col = piece["position"]["row"], piece["position"]["col"]
    return [
        {"row": r, "col": c}
        for r in range(max(0, row - radius), min(state["board"]["rows"], row + radius + 1))
        for c in range(max(0, col - radius), min(state["board"]["cols"], col + radius + 1))
    ]


def blocker_groups(state: dict, pid: str, target: dict) -> list[dict]:
    legal_move(state, pid, target)
    path = trace_path(state["pieces"][pid], target)
    return [
        {"position": pos, "members": members, "path_index": i, "endpoint": i == len(path) - 1}
        for i, pos in enumerate(path[1:], 1)
        if (members := occupants(state, pos, exclude=pid))
    ]


def negotiation_mode(
    state: dict, party_a: list[str], party_b: list[str], endpoint: bool = False
) -> str:
    if endpoint:
        return "equal"
    a_can_b = any(
        can_threaten(state["pieces"][a], state["pieces"][b]["position"])
        for a in party_a
        for b in party_b
    )
    b_can_a = any(
        can_threaten(state["pieces"][b], state["pieces"][a]["position"])
        for a in party_a
        for b in party_b
    )
    return "equal" if a_can_b == b_can_a else "a_advantage" if a_can_b else "b_advantage"


def begin_turn(state: dict) -> None:
    state["rest_at_start"] = {
        pid: p["rest_remaining"] for pid, p in state["pieces"].items() if p["alive"]
    }
    state["locked_this_turn"] = [
        pid for pid, p in state["pieces"].items() if p["alive"] and p["battle_id"]
    ]
    for piece in state["pieces"].values():
        if piece["alive"]:
            piece["rest_remaining"] = max(0, piece["rest_remaining"] - 1)


def commit_move(state: dict, pid: str, target: dict) -> list[dict]:
    """Commit a resolved legal endpoint, including partial sliding displacement."""
    legal_move(state, pid, target)
    piece = live_piece(state, pid)
    origin = dict(piece["position"])
    path = trace_path(piece, target)
    piece["position"] = dict(target)
    piece["rest_remaining"] = movement_rest(piece, origin, target)
    return [
        {
            "type": "MoveCommitted",
            "actor_id": pid,
            "payload": {
                "piece_id": pid,
                "piece_name": piece.get("name", pid),
                "piece_type": piece["type"],
                "origin": origin,
                "position": dict(target),
                "path": path,
                "rest_remaining": piece["rest_remaining"],
            },
        }
    ]
