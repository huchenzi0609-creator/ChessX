"""Bounded spatial recall from owner projections, never from global occupancy.

Coordinates are rules-domain observations. A spoken report is not a sighting;
this module does not parse dialogue into supposedly verified map coordinates.
"""

from copy import deepcopy

from chessx.domain.errors import RuleError
from chessx.rules.geometry import inside_board, movement_rest, trace_path

RECENT_ROUTE_LIMIT = 4
VISITED_CELL_LIMIT = 24
MOVE_OPTION_LIMIT = 12


def relative_position(origin: dict, destination: dict) -> dict:
    """Describe geometric displacement, without promising a legal travel route."""
    dr = destination["row"] - origin["row"]
    dc = destination["col"] - origin["col"]
    # Chinese diagonal bearings conventionally put east/west first: 东北/西南.
    bearing = ("西" if dc < 0 else "东" if dc > 0 else "") + (
        "北" if dr < 0 else "南" if dr > 0 else ""
    )
    distance = max(abs(dr), abs(dc))
    return {
        "bearing": bearing or "同格",
        "delta_row": dr,
        "delta_col": dc,
        "distance_steps": distance,
        "distance_band": "同格" if distance == 0 else "邻近" if distance <= 2 else "较远",
    }


def _receipt_event(receipt: dict) -> tuple[str, dict, dict]:
    event = receipt.get("payload", {})
    if not isinstance(event, dict):
        return "", {}, {}
    payload = event.get("payload", event)
    return (
        receipt.get("channel", event.get("type", "")),
        event,
        payload if isinstance(payload, dict) else {},
    )


def _position(value, board: dict) -> dict | None:
    if isinstance(value, dict) and inside_board({"board": board}, value):
        return {"row": value["row"], "col": value["col"]}
    return None


def pending_move_context(
    observation: dict,
    pid: str,
    stage: str,
    protocol: dict | None,
    work: dict | None,
) -> dict:
    """Describe an admitted local movement contact, not an intermediate pose.

    Only the mover receives its own pending intent. Blockers receive the already
    admitted contact anchor/mode, never the mover's hidden origin or destination.
    """
    if stage != "negotiation":
        return {}
    protocol = protocol if isinstance(protocol, dict) else {}
    work = work if isinstance(work, dict) else {}
    board, own = observation["board"], observation["self"]
    anchor = _position(protocol.get("position"), board)
    result = {}
    if anchor is not None:
        result["interaction_contact"] = {
            "position": [anchor["row"], anchor["col"]],
            "mode": protocol.get("mode"),
            "scope": "Local movement negotiation, not an ordinary meeting or a newly visited cell.",
        }
    intent = work.get("intent", {})
    intent = intent if isinstance(intent, dict) else {}
    target = _position(intent.get("target"), board)
    if (
        work.get("actor_id") != pid
        or own.get("id") != pid
        or intent.get("kind") != "move"
        or target is None
    ):
        return result
    origin = own["position"]
    relative = relative_position(origin, target)
    result["pending_move"] = {
        "planned_target": [target["row"], target["col"]],
        "committed_origin": [origin["row"], origin["col"]],
        "bearing": relative["bearing"],
        "distance_steps": relative["distance_steps"],
        "intent_status": "not_committed_not_visited",
        "pose_semantics": "You are negotiating a local movement contact. self.position stays at the last committed origin until movement resolves.",
    }
    if anchor is not None:
        result["pending_move"].update(
            contact_anchor=[anchor["row"], anchor["col"]],
            contact_kind="co-location" if anchor == target else "passage",
        )
    return result


def _event_order(record: dict) -> tuple[int, int]:
    turn = record.get("last_seen_turn")
    event_id = record.get("source_event_id")
    suffix = event_id.rsplit(":", 1)[-1] if isinstance(event_id, str) else ""
    return turn if type(turn) is int else -1, int(suffix) if suffix.isdecimal() else -1


def spatial_recall(
    observation: dict,
    receipts: list[dict],
    pid: str,
    *,
    turn: int | None = None,
    spatial_memory: dict | None = None,
) -> dict:
    """Reconstruct journeys and last sightings from this owner's archive only."""
    own, board = observation["self"], observation["board"]
    origin = own["position"]
    known: dict[str, dict] = {}
    names: dict[str, str] = {}
    visited: dict[tuple[int, int], dict] = {}
    routes = []

    def remember(who, position, receipt, event, *, scope="last_seen", dead=False):
        position = _position(position, board)
        if not isinstance(who, str) or who == pid or position is None:
            return
        known[who] = {
            "piece_id": who,
            "position": position,
            "current_visible": False,
            "last_seen_turn": event.get("turn"),
            "status_at_sighting": "dead" if dead else "alive",
            "position_scope": scope,
            "source": "owner_receipt",
            **{
                key: receipt[key]
                for key in ("receipt_id", "source_event_id")
                if isinstance(receipt.get(key), str)
            },
        }

    for receipt in receipts:
        channel, event, payload = _receipt_event(receipt)
        who = payload.get("piece_id", payload.get("speaker_id", event.get("actor_id")))
        name = payload.get("piece_name", payload.get("speaker_name"))
        if isinstance(who, str) and isinstance(name, str):
            names[who] = name
        participants = payload.get("participants", [])
        participants = participants if isinstance(participants, list) else []
        for participant in participants:
            if not isinstance(participant, dict):
                continue
            participant_id = participant.get("id", participant.get("piece_id"))
            if isinstance(participant_id, str) and isinstance(participant.get("name"), str):
                names[participant_id] = participant["name"]
            if channel in {"ConversationCreated", "NegotiationStarted"}:
                remember(participant_id, participant.get("position"), receipt, event)

        if channel in {"MoveCommitted", "MovementCommitted", "PieceMoved"}:
            destination = payload.get("position", payload.get("actual_to", payload.get("to")))
            path = payload.get("path", [])
            path = [_position(pos, board) for pos in path] if isinstance(path, list) else []
            path = [pos for pos in path if pos is not None]
            if who == pid:
                start = _position(payload.get("origin", payload.get("from")), board)
                end = _position(destination, board)
                if end is None:
                    continue
                if not path and start is not None:
                    # A knight's route has exactly its two endpoints. Inference
                    # for one's own immutable geometry never inspects occupancy.
                    try:
                        path = trace_path({**own, "position": start}, end)
                    except RuleError:
                        path = [start, end]
                if not path:
                    path = [end]
                route = {
                    "from": deepcopy(start or path[0]),
                    "to": deepcopy(end),
                    "turn": event.get("turn"),
                    "path_length": len(path),
                    "is_jump": own["type"] == "knight",
                    **{
                        key: receipt[key]
                        for key in ("receipt_id", "source_event_id")
                        if isinstance(receipt.get(key), str)
                    },
                    **relative_position(start or path[0], end),
                }
                route["path" if len(path) <= 10 else "path_endpoints"] = [
                    [pos["row"], pos["col"]]
                    for pos in (path if len(path) <= 10 else [path[0], end])
                ]
                routes.append(route)
                for pos in path:
                    key = pos["row"], pos["col"]
                    # Reinsert so the bounded list favors recently visited cells.
                    visited.pop(key, None)
                    visited[key] = {
                        "position": deepcopy(pos),
                        "last_visited_turn": event.get("turn"),
                        **{
                            key: receipt[key]
                            for key in ("receipt_id", "source_event_id")
                            if isinstance(receipt.get(key), str)
                        },
                    }
            else:
                last_position = destination or payload.get("last_visible_position")
                if last_position is None and path:
                    last_position = path[-1]
                remember(
                    who,
                    last_position,
                    receipt,
                    event,
                    scope="last_seen_on_partial_path" if destination is None else "last_seen",
                )
        elif channel in {"DeathCommitted", "PieceDied", "DeathExecuted"}:
            remember(who, payload.get("position"), receipt, event, dead=True)
        elif channel in {"ConversationCreated", "NegotiationStarted"}:
            # In a contact negotiation the mover is still at its origin until
            # MoveCommitted. Only the blocking party occupies the contact cell.
            if payload.get("kind") == "negotiation" or channel == "NegotiationStarted":
                members = payload.get("member_ids", payload.get("members", []))
                mover = event.get("actor_id", payload.get("mover_id"))
                if isinstance(members, list) and isinstance(mover, str):
                    for member in members:
                        if member != mover:
                            remember(member, payload.get("position"), receipt, event)

    # Persisted sightings include stationary people seen in earlier turns, which
    # movement receipts alone cannot recall. This dictionary is owner-scoped.
    saved = (spatial_memory or {}).get("seen_people", {})
    if isinstance(saved, dict):
        for who, record in saved.items():
            if not isinstance(who, str) or who == pid or not isinstance(record, dict):
                continue
            position = _position(record.get("position"), board)
            if position is None:
                continue
            if isinstance(record.get("name"), str):
                names[who] = record["name"]
            entry = {
                "piece_id": who,
                "position": position,
                "current_visible": False,
                "last_seen_turn": record.get("last_seen_turn"),
                "status_at_sighting": record.get("status_at_sighting", "alive"),
                "position_scope": "last_seen",
                "source": "owner_spatial_memory",
                **{
                    key: record[key]
                    for key in ("source_event_id", "receipt_id")
                    if isinstance(record.get(key), str)
                },
            }
            if who not in known or _event_order(entry) >= _event_order(known[who]):
                known[who] = entry
    for piece in observation.get("pieces", []):
        if piece["id"] != pid:
            known[piece["id"]] = {
                "piece_id": piece["id"],
                "position": deepcopy(piece["position"]),
                "current_visible": True,
                "last_seen_turn": turn,
                "status_at_sighting": "alive",
                "position_scope": "current_view",
                "source": "current_observation",
            }
            names[piece["id"]] = piece["name"]
    for who, entry in known.items():
        if who in names:
            entry["name"] = names[who]
        last_turn = entry.get("last_seen_turn")
        entry["age_turns"] = (
            max(0, turn - last_turn) if type(turn) is int and type(last_turn) is int else None
        )
        entry.update(relative_position(origin, entry["position"]))
    current_key = origin["row"], origin["col"]
    visited.pop(current_key, None)
    visited[current_key] = {"position": deepcopy(origin), "last_visited_turn": turn}
    last_route = routes[-1] if routes else None
    return {
        "self_journey": {
            "current_position": deepcopy(origin),
            "previous_position": deepcopy(last_route["from"]) if last_route else None,
            "arrival_from": (
                relative_position(origin, last_route["from"])["bearing"] if last_route else None
            ),
            "recent_actual_routes": routes[-RECENT_ROUTE_LIMIT:],
            "scope": "Actual own committed travel only; proposals and contact cells are not visited. The complete source receipts remain archived.",
        },
        "visited_cells": list(visited.values())[-VISITED_CELL_LIMIT:],
        "visited_total_count": len(visited),
        "known_locations": sorted(
            known.values(),
            key=lambda entry: (
                not entry["current_visible"],
                entry["age_turns"] or 0,
                entry["piece_id"],
            ),
        ),
    }


def movement_options(
    observation: dict, targets: list[list[int]], *, rules_state: dict | None = None
) -> list[dict]:
    """Summarize representative routes using current visible blockers only.

    This is deliberately advisory. Unknown cells remain unknown, and neither a
    stale last sighting nor a reported direction is a current route obstruction.
    """
    own = observation["self"]
    visible = {(cell["row"], cell["col"]) for cell in observation.get("visible_cells", [])}
    occupied: dict[tuple[int, int], list[str]] = {}
    for piece in observation.get("pieces", []):
        if piece["id"] != own["id"]:
            position = piece["position"]
            occupied.setdefault((position["row"], position["col"]), []).append(piece["id"])
    # Represent low-cost movement, including nearby occupied alternatives so
    # their contact cost is explicit. The complete geometric list remains intact.
    ordered = sorted(
        targets,
        key=lambda target: (
            max(abs(target[0] - own["position"]["row"]), abs(target[1] - own["position"]["col"])),
            tuple(target) in occupied,
            target,
        ),
    )
    options = []
    for row, col in ordered[:MOVE_OPTION_LIMIT]:
        destination = {"row": row, "col": col}
        path = trace_path(own, destination)[1:]
        contacts = [
            {
                "position": [pos["row"], pos["col"]],
                "occupants": occupied[(pos["row"], pos["col"])],
                "at_destination": pos == destination,
            }
            for pos in path
            if (pos["row"], pos["col"]) in occupied
        ]
        knowledge = (
            "visible_occupied"
            if (row, col) in occupied
            else "visible_empty"
            if (row, col) in visible
            else "unknown"
        )
        options.append(
            {
                "target": [row, col],
                "bearing": relative_position(own["position"], destination)["bearing"],
                "distance_steps": max(
                    abs(row - own["position"]["row"]), abs(col - own["position"]["col"])
                ),
                "rest_turns_if_success": movement_rest(
                    own, own["position"], destination, rules_state
                ),
                "destination_knowledge": knowledge,
                "known_contacts": contacts,
                "unknown_path_count": sum((pos["row"], pos["col"]) not in visible for pos in path),
            }
        )
    return options


def approach_options(
    observation: dict,
    targets: list[list[int]],
    known_locations: list[dict],
    *,
    rules_state: dict | None = None,
) -> list[dict]:
    """Link personally known places to optional legal next moves, without goals.

    Reaching a past sighting is exploration of that remembered place, never
    live pursuit of someone whose actual whereabouts are outside observation.
    """
    own = observation["self"]
    # Route knowledge is independent of the person being approached. Cache the
    # bounded summaries once instead of tracing the same candidates per person.
    routes = {
        tuple(target): movement_options(observation, [target], rules_state=rules_state)[0]
        for target in targets
    }
    approaches = []
    for entry in known_locations[:8]:
        reference = entry["position"]
        before = relative_position(own["position"], reference)["distance_steps"]
        axis_before = abs(own["position"]["row"] - reference["row"]) + abs(
            own["position"]["col"] - reference["col"]
        )
        candidates = []
        for target in targets:
            after = max(abs(target[0] - reference["row"]), abs(target[1] - reference["col"]))
            progress = before - after
            axis_after = abs(target[0] - reference["row"]) + abs(target[1] - reference["col"])
            axis_progress = axis_before - axis_after
            # Orthogonal pieces approaching a diagonal place first reduce one
            # axis while Chebyshev distance stays equal. Neither metric may
            # increase, and at least one must improve; no aimless detours.
            if progress < 0 or axis_progress < 0 or (progress == 0 and axis_progress == 0):
                continue
            route = routes[tuple(target)]
            candidates.append(
                {
                    "target": list(target),
                    "bearing": route["bearing"],
                    "distance_after": after,
                    "progress_steps": progress,
                    "axis_progress_steps": axis_progress,
                    "rest_turns_if_success": route["rest_turns_if_success"],
                    "known_contact_ids": sorted(
                        {who for contact in route["known_contacts"] for who in contact["occupants"]}
                    ),
                    "unknown_path_count": route["unknown_path_count"],
                }
            )
        # A free nearby approach can permit an invitation without gratuitously
        # negotiating co-location. Contact remains an available lawful choice.
        candidates.sort(
            key=lambda candidate: (
                bool(candidate["known_contact_ids"]),
                -candidate["progress_steps"],
                -candidate["axis_progress_steps"],
                candidate["rest_turns_if_success"],
                candidate["unknown_path_count"],
                candidate["target"],
            )
        )
        approaches.append(
            {
                "piece_id": entry["piece_id"],
                "reference_position": [reference["row"], reference["col"]],
                "position_scope": entry["position_scope"],
                "already_visible": entry["current_visible"],
                "distance_before": before,
                "options": candidates[:2],
            }
        )
    return approaches
