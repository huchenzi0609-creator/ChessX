"""Durable ordinary conversations: one model response per execution step.

The runner persists the mutated work cursor and drafts atomically. This mixin
owns membership/protocol scheduling; it never writes storage or calls providers.
"""

from copy import deepcopy
from uuid import uuid4

from chessx.domain.errors import RuleError
from chessx.rules.actions import event
from chessx.rules.geometry import visible_positions

STAY_SCHEMA = {
    "type": "object",
    "properties": {"stay": {"type": "boolean"}},
    "required": ["stay"],
    "additionalProperties": False,
}
VOTE_SCHEMA = {
    "type": "object",
    "properties": {"approve": {"type": "boolean"}},
    "required": ["approve"],
    "additionalProperties": False,
}
ORDINARY_SCHEMA = {
    "type": "object",
    "properties": {
        "kind": {"enum": ["say", "pass", "leave", "propose_add", "propose_kick"]},
        "speech": {"type": "string", "minLength": 1},
        "target_id": {"type": "string", "minLength": 1},
    },
    "required": ["kind"],
    "additionalProperties": False,
}


class DialogueMixin:
    def _close_conversation(self, state: dict, conv: dict, reason: str) -> list[dict]:
        audience = list(conv["members"])
        for pid in audience:
            if state["pieces"][pid].get("conversation_id") == conv["id"]:
                state["pieces"][pid]["conversation_id"] = None
        conv.update(status="closed", members=[], pending_proposal=None)
        return [
            event(
                "ConversationClosed",
                None,
                {"conversation_id": conv["id"], "reason": reason},
                audience,
            )
        ]

    def _leave_ordinary(self, state: dict, conv: dict, pid: str) -> list[dict]:
        audience = list(conv["members"])
        conv["members"] = [member for member in audience if member != pid]
        state["pieces"][pid]["conversation_id"] = None
        conv["pending_proposal"] = None
        drafts = [
            event(
                "MembershipChanged",
                pid,
                {"conversation_id": conv["id"], "piece_id": pid, "change": "left"},
                audience,
            )
        ]
        if len(conv["members"]) < 2:
            drafts.extend(self._close_conversation(state, conv, "insufficient_members"))
        return drafts

    def _invite_finish(self, state: dict, work: dict) -> None:
        work["done"] = True
        if not work.get("governance"):
            self.finish_primary(state)

    async def invite_step(self, state: dict, work: dict) -> list[dict]:
        if work.get("stage", "init") == "init":
            actor, target = work["actor_id"], work["intent"]["target_id"]
            pieces = state["pieces"]
            if actor == target or target not in pieces or not pieces[target]["alive"]:
                raise RuleError(
                    "INVITE_TARGET_INVALID", "Invite requires another living visible piece"
                )
            if pieces[target]["position"] not in visible_positions(state, actor):
                raise RuleError("TARGET_NOT_VISIBLE", "Invite target must be visible")
            if state.get("rest_at_start", {}).get(
                actor, pieces[actor]["rest_remaining"]
            ) > 0 and not work.get("governance"):
                raise RuleError("PIECE_RESTING", "A resting piece cannot actively invite")
            if pieces[actor].get("battle_id") or pieces[target].get("battle_id"):
                self._invite_finish(state, work)
                return [
                    event(
                        "InvitationUnavailable",
                        actor,
                        {"target_id": target, "reason": "target_in_battle"},
                        [actor],
                    )
                ]
            a_id, b_id = pieces[actor].get("conversation_id"), pieces[target].get("conversation_id")
            if a_id and a_id == b_id:
                self._invite_finish(state, work)
                return [
                    event(
                        "InvitationUnavailable",
                        actor,
                        {"target_id": target, "reason": "already_member"},
                        [actor],
                    )
                ]
            parents = [state["conversations"][cid] for cid in dict.fromkeys([a_id, b_id]) if cid]
            own_party = list(state["conversations"][a_id]["members"]) if a_id else [actor]
            target_party = list(state["conversations"][b_id]["members"]) if b_id else [target]
            members = list(dict.fromkeys([actor] + own_party + target_party))
            counted = set().union(
                *(set(parent.get("counted_message_ids", [])) for parent in parents)
            )
            kicks = set().union(*(set(parent.get("kick_used_by", [])) for parent in parents))
            last_spoke = {}
            for parent in parents:
                for pid, turn in parent.get("last_spoke_turn", {}).items():
                    last_spoke[pid] = max(last_spoke.get(pid, -1), turn)
            drafts = []
            for parent in parents:
                drafts.extend(self._close_conversation(state, parent, "merged"))
            if len(counted) >= state["limits"]["conversation_max_messages"]:
                self._invite_finish(state, work)
                return drafts + [
                    event(
                        "InvitationUnavailable",
                        actor,
                        {"target_id": target, "reason": "message_budget_exhausted"},
                        members,
                    )
                ]
            cid = f"conversation_{uuid4().hex}"
            conv = {
                "id": cid,
                "kind": "ordinary",
                "status": "active",
                "members": members,
                "initiator_id": actor,
                "created_turn": state["turn"],
                "parent_ids": [p["id"] for p in parents],
                "counted_message_ids": sorted(counted),
                "messages": [],
                "kick_used_by": sorted(kicks),
                "last_spoke_turn": last_spoke,
                "governance_turn": max((p.get("governance_turn", -1) for p in parents), default=-1),
                "pending_proposal": None,
                "idle_turns": 0,
                "last_idle_turn": -1,
                "last_activity_turn": max(
                    (p.get("last_activity_turn", -1) for p in parents), default=-1
                ),
            }
            state["conversations"][cid] = conv
            for pid in members:
                pieces[pid]["conversation_id"] = cid
            work.update(
                stage="responses", conversation_id=cid, invited_ids=target_party, response_index=0
            )
            drafts.append(
                event(
                    "ConversationCreated",
                    actor,
                    {"conversation_id": cid, "members": members, "parent_ids": conv["parent_ids"]},
                    members,
                )
            )
            return drafts
        cid = work["conversation_id"]
        conv = state["conversations"][cid]
        invited = work["invited_ids"]
        index = work["response_index"]
        if conv["status"] != "active" or index >= len(invited):
            self._invite_finish(state, work)
            return []
        pid = invited[index]
        if pid not in conv["members"]:
            work["response_index"] += 1
            return []
        answer = await self.ask(
            state,
            pid,
            "invite_response",
            f"turn:{state['turn']}:invite:{cid}:{pid}",
            STAY_SCHEMA,
            {
                "conversation_id": cid,
                "member_ids": list(conv["members"]),
                "instruction": "You have joined. Choose stay or leave before the first speech.",
            },
        )
        work["response_index"] += 1
        drafts = [
            event(
                "InvitationResponded",
                pid,
                {
                    "conversation_id": cid,
                    "piece_id": pid,
                    "status": "stayed" if answer["stay"] else "left",
                },
                list(conv["members"]),
            )
        ]
        if not answer["stay"]:
            drafts.extend(self._leave_ordinary(state, conv, pid))
        return drafts

    async def ordinary_step(self, state: dict, cid: str, work: dict) -> list[dict]:
        cid = work.get("conversation_id", cid)
        stage = work.get("stage", "speak")
        if stage == "invite":
            nested = work["invite_work"]
            drafts = await self.invite_step(state, nested)
            if nested.get("done"):
                work.update(
                    stage="speak",
                    conversation_id=nested.get("conversation_id", cid),
                    member_index=0,
                )
                work.pop("member_order", None)
            return drafts
        conv = state["conversations"].get(cid)
        if not conv or conv["status"] != "active":
            work["done"] = True
            return []
        if stage == "vote":
            return await self._ordinary_vote(state, conv, work)
        order = work.setdefault("member_order", list(conv["members"]))
        index = work.setdefault("member_index", 0)
        if index >= len(order):
            if conv.get("last_idle_turn") != state["turn"]:
                active = conv.get("last_activity_turn") == state["turn"]
                conv["idle_turns"] = 0 if active else conv.get("idle_turns", 0) + 1
                conv["last_idle_turn"] = state["turn"]
            work["done"] = True
            return (
                self._close_conversation(state, conv, "two_idle_turns")
                if conv["idle_turns"] >= 2
                else []
            )
        pid = order[index]
        if (
            pid not in conv["members"]
            or conv.setdefault("last_spoke_turn", {}).get(pid) == state["turn"]
        ):
            work["member_index"] += 1
            return []

        def validate(answer: dict) -> None:
            kind = answer["kind"]
            if kind == "say" and not answer.get("speech"):
                raise RuleError("SPEECH_REQUIRED", "Saying requires nonempty speech")
            if kind == "pass" and answer.get("speech"):
                raise RuleError("PASS_MUST_BE_SILENT", "A silent pass cannot contain speech")
            if kind.startswith("propose_"):
                target = answer.get("target_id")
                if conv.get("governance_turn", -1) == state["turn"]:
                    raise RuleError(
                        "GOVERNANCE_QUOTA_USED",
                        "Only one governance proposal per conversation and turn is permitted",
                    )
                if kind == "propose_kick":
                    if (
                        pid in conv.get("kick_used_by", [])
                        or target not in conv["members"]
                        or target == pid
                    ):
                        raise RuleError(
                            "KICK_PROPOSAL_INVALID", "Kick target or proposal quota is invalid"
                        )
                elif (
                    target not in state["pieces"]
                    or not state["pieces"][target]["alive"]
                    or target in conv["members"]
                    or state["pieces"][target]["position"] not in visible_positions(state, pid)
                    or state["pieces"][target].get("battle_id")
                ):
                    raise RuleError(
                        "ADD_PROPOSAL_INVALID",
                        "Add target must be a living visible nonmember outside battle",
                    )

        answer = await self.ask(
            state,
            pid,
            "ordinary",
            f"turn:{state['turn']}:ordinary:{cid}:{pid}",
            ORDINARY_SCHEMA,
            {
                "conversation_id": cid,
                "member_ids": list(conv["members"]),
                "message_count": len(conv["counted_message_ids"]),
                "instruction": "Talk as this person, not as an investigator giving a report. Read dialogue_recall, address one new point, vary length with actual content. Do not echo plans or interrogate by default. Use pass for nothing new or leave if you want to go; physical movement is available at your next eligible primary action, not through speech. Never say source/citation numbers.",
                "remaining_messages": state["limits"]["conversation_max_messages"]
                - len(conv["counted_message_ids"]),
            },
            validator=validate,
        )
        conv["last_spoke_turn"][pid] = state["turn"]
        work["member_index"] += 1
        drafts = []
        if answer.get("speech"):
            message_id = f"message_{uuid4().hex}"
            message = {
                "message_id": message_id,
                "conversation_id": cid,
                "speaker_id": pid,
                "speech": answer["speech"],
            }
            conv.setdefault("messages", []).append(deepcopy(message))
            conv["counted_message_ids"].append(message_id)
            conv["last_activity_turn"] = state["turn"]
            drafts.append(event("ConversationMessage", pid, message, list(conv["members"])))
            if len(conv["counted_message_ids"]) >= state["limits"]["conversation_max_messages"]:
                return drafts + self._close_conversation(state, conv, "message_limit")
        if answer["kind"] == "leave":
            return drafts + self._leave_ordinary(state, conv, pid)
        if answer["kind"] in {"propose_add", "propose_kick"}:
            target = answer["target_id"]
            voters = list(conv["members"])
            conv["governance_turn"] = state["turn"]
            if answer["kind"] == "propose_kick":
                conv.setdefault("kick_used_by", []).append(pid)
                voters = [member for member in voters if member not in {pid, target}]
            proposal = {
                "id": f"proposal_{uuid4().hex}",
                "kind": answer["kind"],
                "actor_id": pid,
                "target_id": target,
                "members_snapshot": list(conv["members"]),
                "voters": voters,
                "votes": {},
            }
            conv["pending_proposal"] = proposal
            work.update(stage="vote", vote_index=0)
            drafts.append(
                event(
                    "GovernanceProposal",
                    pid,
                    {
                        "conversation_id": cid,
                        "kind": answer["kind"],
                        "target_id": target,
                        "member_ids": self._expanded_add_members(state, target)
                        if answer["kind"] == "propose_add"
                        else voters,
                    },
                    list(conv["members"]),
                )
            )
        elif not drafts:
            drafts.append(
                event(
                    "ConversationPassed",
                    pid,
                    {"conversation_id": cid, "piece_id": pid},
                    list(conv["members"]),
                )
            )
        return drafts

    def _expanded_add_members(self, state: dict, target: str) -> list[str]:
        cid = state["pieces"][target].get("conversation_id")
        return list(state["conversations"][cid]["members"]) if cid else [target]

    async def _ordinary_vote(self, state: dict, conv: dict, work: dict) -> list[dict]:
        proposal = conv.get("pending_proposal")
        if not proposal or proposal["members_snapshot"] != conv["members"]:
            conv["pending_proposal"] = None
            work["stage"] = "speak"
            return [
                event(
                    "GovernanceResolved",
                    None,
                    {"conversation_id": conv["id"], "reason": "membership_changed"},
                    list(conv["members"]),
                )
            ]
        index = work["vote_index"]
        if index < len(proposal["voters"]):
            pid = proposal["voters"][index]
            answer = await self.ask(
                state,
                pid,
                "vote",
                f"turn:{state['turn']}:vote:{proposal['id']}:{pid}",
                VOTE_SCHEMA,
                {
                    "conversation_id": conv["id"],
                    "proposal": {
                        "kind": proposal["kind"],
                        "actor_id": proposal["actor_id"],
                        "target_id": proposal["target_id"],
                        "member_ids": self._expanded_add_members(state, proposal["target_id"])
                        if proposal["kind"] == "propose_add"
                        else list(proposal["voters"]),
                    },
                },
            )
            proposal["votes"][pid] = answer["approve"]
            work["vote_index"] += 1
            return [
                event(
                    "VoteCast",
                    pid,
                    {
                        "conversation_id": conv["id"],
                        "proposal_id": proposal["id"],
                        "approve": answer["approve"],
                    },
                    [pid],
                )
            ]
        approved = bool(proposal["voters"]) and all(
            proposal["votes"].get(pid) is True for pid in proposal["voters"]
        )
        conv["pending_proposal"] = None
        work["stage"] = "speak"
        drafts = [
            event(
                "GovernanceResolved",
                proposal["actor_id"],
                {
                    "conversation_id": conv["id"],
                    "target_id": proposal["target_id"],
                    "result": "approved" if approved else "not_approved",
                },
                list(conv["members"]),
            )
        ]
        if approved and proposal["kind"] == "propose_kick":
            drafts.extend(self._leave_ordinary(state, conv, proposal["target_id"]))
        elif approved:
            work.update(
                stage="invite",
                invite_work={
                    "kind": "invite",
                    "stage": "init",
                    "actor_id": proposal["actor_id"],
                    "intent": {"kind": "invite", "target_id": proposal["target_id"]},
                    "governance": True,
                },
            )
        return drafts
