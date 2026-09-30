"""Resumable movement and bounded, shared-message negotiation protocol.

Only the persisted work cursor advances between steps. Until the final commit,
the mover stays at its origin; model latency never creates an intermediate pose.
"""

from copy import deepcopy
from uuid import uuid4

from chessx.domain.errors import RuleError
from chessx.rules.actions import (
    authorize_join,
    event,
    leave_conversation,
    merge_battles,
)
from chessx.rules.geometry import (
    blocker_groups,
    commit_move,
    legal_move,
    negotiation_mode,
    trace_path,
)


def negotiation_schema(allowed: list[str]) -> dict:
    return {
        "type": "object",
        "properties": {
            "kind": {"type": "string", "enum": ["say", "pass", "leave"]},
            "speech": {"type": "string", "minLength": 1},
            "proposal": {"type": "string", "enum": [*allowed, "undecided"]},
        },
        "required": ["kind"],
        "additionalProperties": False,
    }


def contact(state: dict, members: list[str]) -> list[dict]:
    """Only the current contact identities, never later path blockers or inventories."""
    return [
        {key: deepcopy(state["pieces"][pid][key]) for key in ("id", "name", "type", "color")}
        for pid in members
    ]


class MovementMixin:
    async def move_step(self, state: dict, work: dict) -> list[dict]:
        stage = work["stage"]
        if stage == "init":
            return self._move_init(state, work)
        if stage == "group_init":
            return self._group_init(state, work)
        if stage == "permission":
            return await self._negotiate_permission(state, work)
        if stage == "result":
            return self._group_result(state, work)
        if stage == "commit":
            return self._move_commit(state, work)
        raise RuleError("MOVEMENT_CURSOR_INVALID", "The movement cursor has an unknown stage")

    def _move_init(self, state: dict, work: dict) -> list[dict]:
        pid, intent = work["actor_id"], work["intent"]
        target = intent["target"]
        legal_move(state, pid, target)
        arrival = intent.get("arrival_action")
        if arrival is not None:
            self._arrival_battle(state, arrival["battle_id"], target)
        piece = state["pieces"][pid]
        origin = deepcopy(piece["position"])
        if origin == target:
            # A zero displacement creates no fresh origin conflict or battle reset.
            self.finish_primary(state)
            return [event("PieceWaited", pid, {"piece_id": pid})]
        groups = blocker_groups(state, pid, target)
        for group in groups:
            group["mode"] = negotiation_mode(
                state, [pid], group["members"], endpoint=group["endpoint"]
            )
            group["limited_active"] = any(
                state["pieces"][member]["battle_id"] is not None for member in group["members"]
            )
        work.update(
            origin=origin,
            path=trace_path(piece, target),
            groups=groups,
            group_index=0,
            stage="group_init",
        )
        drafts = leave_conversation(state, pid)
        drafts.append(
            event("MoveProposed", pid, {"piece_id": pid, "target": deepcopy(target)}, [pid])
        )
        return drafts

    @staticmethod
    def _arrival_battle(state: dict, battle_id: str, target: dict) -> dict:
        battle = state["battles"].get(battle_id)
        if battle is None or battle["status"] != "active":
            raise RuleError("BATTLE_NOT_ACTIVE", "The arrival battle is no longer active")
        if battle["anchor"] != target:
            raise RuleError(
                "BATTLE_NOT_AT_ANCHOR", "An arrival action must target the battle anchor"
            )
        return battle

    def _group_init(self, state: dict, work: dict) -> list[dict]:
        index = work["group_index"]
        if index >= len(work["groups"]):
            work["destination"] = deepcopy(work["intent"]["target"])
            work["stage"] = "commit"
            return []
        group = work["groups"][index]
        if group["endpoint"] and work["intent"].get("arrival_action"):
            # Physical arrival replaces ordinary target coexistence negotiation.
            work["destination"] = deepcopy(work["intent"]["target"])
            work["stage"] = "commit"
            return []
        pid = work["actor_id"]
        members = [pid, *sorted(group["members"])]
        cid = f"neg_{uuid4().hex}"
        allowed = ["coexist", "return_mover"] if group["endpoint"] else ["pass", "stop"]
        if not group["limited_active"]:
            allowed.append("fight")
        mode = group["mode"]
        deciding = (
            [pid]
            if mode == "a_advantage"
            else group["members"]
            if mode == "b_advantage"
            else members
        )
        conversation = {
            "id": cid,
            "kind": "negotiation",
            "status": "active",
            "members": members,
            "message_count": 0,
            "counted_message_ids": [],
            "messages": [],
            "proposals": {},
            "permission_index": 0,
            "idle_cycles": 0,
            "cycle_has_speech": False,
            "allowed_results": allowed,
            "decision_party": list(deciding),
            "mode": mode,
            "position": deepcopy(group["position"]),
            "created_turn": state["turn"],
            "max_messages": state["limits"]["conversation_max_messages"],
        }
        state["conversations"][cid] = conversation
        work.update(conversation_id=cid, stage="permission")
        return [
            event(
                "ConversationCreated",
                pid,
                {
                    "conversation_id": cid,
                    "kind": "negotiation",
                    "members": members,
                    "member_ids": members,
                    "participants": contact(state, members),
                    "position": deepcopy(group["position"]),
                    "mode": mode,
                },
                members,
            )
        ]

    async def _negotiate_permission(self, state: dict, work: dict) -> list[dict]:
        cid = work["conversation_id"]
        conv = state["conversations"][cid]
        index = conv["permission_index"]
        members = conv["members"]
        pid = members[index % len(members)]
        allowed = conv["allowed_results"]

        def validate(reply: dict) -> None:
            kind = reply.get("kind")
            if kind == "say" and not reply.get("speech", "").strip():
                raise RuleError("SPEECH_EMPTY", "A say response needs nonempty speech")
            if kind != "say" and "speech" in reply:
                raise RuleError(
                    "SPEECH_NOT_AUTHORIZED", "Silent pass and leave cannot contain speech"
                )
            if reply.get("proposal", "undecided") not in [*allowed, "undecided"]:
                code = (
                    "BATTLE_ALREADY_ACTIVE"
                    if reply.get("proposal") == "fight"
                    else "NEGOTIATION_RESULT_INVALID"
                )
                raise RuleError(code, "The proposed result is not authorized for this contact")

        protocol = {
            "conversation_id": cid,
            "kind": "negotiation",
            "members": contact(state, members),
            "allowed_results": allowed,
            "mode": conv["mode"],
            "decision_party": conv["decision_party"],
            "position": deepcopy(conv["position"]),
            "message_count": conv["message_count"],
            "max_messages": conv["max_messages"],
            "messages": deepcopy(conv["messages"]),
            "latest_proposals": deepcopy(conv["proposals"]),
        }
        reply = await self.ask(
            state,
            pid,
            "negotiation",
            f"turn:{state['turn']}:primary:{work['actor_id']}:move:{work['group_index']}:permission:{index}",
            negotiation_schema(allowed),
            protocol,
            validator=validate,
        )
        # A failed or obsolete model operation never reaches these mutations.
        validate(reply)
        drafts = []
        conv["permission_index"] += 1
        if reply["kind"] == "say":
            message_id = f"{cid}:{index}"
            message = {
                "id": message_id,
                "message_id": message_id,
                "speaker_id": pid,
                "speech": reply["speech"],
            }
            conv["messages"].append(message)
            conv["counted_message_ids"].append(message_id)
            conv["message_count"] += 1
            conv["cycle_has_speech"] = True
            drafts.append(
                event(
                    "NegotiationMessage",
                    pid,
                    {
                        "conversation_id": cid,
                        "message_id": message_id,
                        "speaker_id": pid,
                        "speech": reply["speech"],
                        "proposal": reply.get("proposal", "undecided"),
                    },
                    members,
                )
            )
        else:
            drafts.append(
                event(
                    "NegotiationPermissionUsed",
                    pid,
                    {
                        "conversation_id": cid,
                        "piece_id": pid,
                        "kind": reply["kind"],
                        "proposal": reply.get("proposal", "undecided"),
                    },
                    members,
                )
            )
        conv["proposals"][pid] = reply.get("proposal", "undecided")
        deciding = conv["decision_party"]
        latest = [conv["proposals"].get(member, "undecided") for member in deciding]
        agreed = (
            latest[0] if latest and latest[0] != "undecided" and len(set(latest)) == 1 else None
        )
        if conv["permission_index"] % len(members) == 0:
            conv["idle_cycles"] = 0 if conv["cycle_has_speech"] else conv["idle_cycles"] + 1
            conv["cycle_has_speech"] = False
        # The final permitted message can establish a result; test it first.
        if agreed is not None:
            work["result"] = agreed
        elif (
            reply["kind"] == "leave"
            or conv["message_count"] >= conv["max_messages"]
            or conv["idle_cycles"] >= 2
        ):
            group = work["groups"][work["group_index"]]
            work["result"] = (
                ("return_mover" if group["endpoint"] else "stop")
                if group["limited_active"]
                else "fight"
            )
        else:
            return drafts
        conv["status"] = "closed"
        work["stage"] = "result"
        drafts.append(
            event(
                "NegotiationResolved",
                work["actor_id"],
                {
                    "conversation_id": cid,
                    "result": work["result"],
                    "position": deepcopy(conv["position"]),
                },
                members,
            )
        )
        return drafts

    def _group_result(self, state: dict, work: dict) -> list[dict]:
        group = work["groups"][work["group_index"]]
        result = work["result"]
        if result in {"pass", "coexist"}:
            work["group_index"] += 1
            work["stage"] = "group_init"
            return []
        if result == "return_mover":
            dest = deepcopy(work["origin"])
        else:
            dest = deepcopy(work["path"][group["path_index"] - 1])
        # A knight's only predecessor is its origin.
        work["destination"] = dest
        work["stage"] = "commit"
        work["arrival_cancelled"] = True
        if result == "fight":
            work["fight_group"] = deepcopy(group)
        return []

    def _move_commit(self, state: dict, work: dict) -> list[dict]:
        pid = work["actor_id"]
        dest = work["destination"]
        arrival = work["intent"].get("arrival_action")
        battle_id = None
        if arrival is not None and not work.get("arrival_cancelled"):
            self._arrival_battle(state, arrival["battle_id"], dest)
            battle_id = arrival["battle_id"]
        drafts = []
        if battle_id is not None:
            parents = [
                bid
                for bid, battle in state["battles"].items()
                if battle["status"] == "active" and battle["anchor"] == dest
            ]
            if len(parents) >= 2:
                battle_id, merged = merge_battles(state, parents, dest)
                drafts.extend(merged)
        drafts.extend(commit_move(state, pid, dest))
        if work.get("fight_group"):
            drafts.extend(self._establish_battle(state, pid, work["fight_group"]))
        elif battle_id is not None:
            drafts.extend(authorize_join(state, pid, battle_id))
        self.finish_primary(state)
        return drafts

    @staticmethod
    def _establish_battle(state: dict, mover: str, group: dict) -> list[dict]:
        members = [mover, *group["members"]]
        if any(state["pieces"][pid]["battle_id"] is not None for pid in members):
            raise RuleError(
                "BATTLE_ALREADY_ACTIVE", "A participant cannot be added to a second battle"
            )
        drafts = []
        for pid in members:
            old_cid = state["pieces"][pid].get("conversation_id")
            old_audience = list(state["conversations"][old_cid]["members"]) if old_cid else []
            leaving = leave_conversation(state, pid)
            for draft in leaving:
                draft["audience"] = old_audience
            drafts.extend(leaving)
        bid = f"battle_{uuid4().hex}"
        anchor = deepcopy(group["position"])
        state["battles"][bid] = {
            "id": bid,
            "status": "active",
            "anchor": anchor,
            "members": members,
            "round": 0,
            "max_rounds": state["limits"]["battle_max_rounds"],
            "created_turn": state["turn"],
            "narratives": [],
            "public_narratives": [],
            "surrendered": [],
        }
        locks = state.setdefault("locked_this_turn", [])
        for pid in members:
            state["pieces"][pid]["battle_id"] = bid
            if pid not in locks:
                locks.append(pid)
        payload = {
            "battle_id": bid,
            "anchor": anchor,
            "status": "active",
            "participants": contact(state, members),
            "participant_ids": members,
        }
        drafts.append(event("BattleStarted", mover, payload, members))
        audience = [
            pid
            for pid, piece in state["pieces"].items()
            if piece["alive"]
            and max(
                abs(piece["position"]["row"] - anchor["row"]),
                abs(piece["position"]["col"] - anchor["col"]),
            )
            <= 2
        ]
        drafts.append(event("BattleNotice", None, deepcopy(payload), audience))
        return drafts
