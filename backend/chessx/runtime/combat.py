"""Incremental combat: no HP, dice, remote joins or automatic deaths."""

from copy import deepcopy
from uuid import uuid4

from chessx.contexts import battle_context
from chessx.domain.errors import RuleError
from chessx.rules.actions import (
    apply_settlement_action,
    kill_loser,
    release_loser,
    resolve_transfer,
    validate_battle_partition,
)
from chessx.runtime import schemas


def draft(kind, actor=None, payload=None, audience=None):
    return {"type": kind, "actor_id": actor, "payload": payload or {}, "audience": audience or []}


class CombatMixin:
    async def combat_step(self, state: dict, work: dict) -> list[dict]:
        battle = state["battles"][work["battle_id"]]
        bid = battle["id"]
        stage = work["stage"]
        members = list(battle["members"])
        if stage == "narrate":
            context = battle_context(state, battle)
            result = await self.ask(
                state,
                None,
                "battle_narrator",
                f"battle:{bid}:round:{battle['round'] + 1}:narrative",
                schemas.NARRATIVE,
                {},
                context=context,
            )
            battle["round"] += 1
            battle.setdefault("narratives", []).append(
                {
                    "round": battle["round"],
                    "narrative": result["narrative"],
                    "public_narrative": result["public_narrative"],
                }
            )
            battle.setdefault("public_narratives", []).append(result["public_narrative"])
            battle.setdefault("round_decisions", {})[str(battle["round"])] = {}
            work.update(stage="surrender", member_index=0)
            return [
                draft(
                    "BattleNarrated",
                    payload={
                        "battle_id": bid,
                        "round": battle["round"],
                        "public_narrative": result["public_narrative"],
                    },
                    audience=members,
                )
            ]
        if stage == "surrender":
            index = work["member_index"]
            if index >= len(members):
                work["stage"] = "outcome" if battle["round"] >= battle["max_rounds"] else "done"
                return [
                    draft(
                        "BattleRoundCompleted",
                        payload={"battle_id": bid, "round": battle["round"]},
                        audience=members,
                    )
                ]
            pid = members[index]
            response = await self.ask(
                state,
                pid,
                "surrender",
                f"battle:{bid}:round:{battle['round']}:surrender:{pid}",
                schemas.SURRENDER,
                {
                    "battle_id": bid,
                    "round": battle["round"],
                    "public_narrative": battle["public_narratives"][-1],
                    "narratives": battle["public_narratives"],
                    "instruction": "Choose surrender or continue from your own remembered perspective.",
                },
            )
            work["member_index"] += 1
            decisions = battle.setdefault("round_decisions", {}).setdefault(
                str(battle["round"]), {}
            )
            decisions[pid] = "surrender" if response["surrender"] else "continue"
            if response["surrender"]:
                battle.setdefault("surrendered", []).append(pid)
                for unasked in members[index + 1 :]:
                    decisions[unasked] = "not_asked"
                work["stage"] = "outcome"
            return [
                draft(
                    "SurrenderDeclared" if response["surrender"] else "CombatContinued",
                    pid,
                    {"battle_id": bid, "piece_id": pid},
                    members,
                )
            ]
        if stage == "outcome":
            context = battle_context(state, battle)
            context["protocol"] = {
                "participant_ids": members,
                "surrendered": battle.get("surrendered", []),
            }

            def validate(result):
                validate_battle_partition(
                    members, result["winners"], result["losers"], battle.get("surrendered", [])
                )

            result = await self.ask(
                state,
                None,
                "outcome",
                f"battle:{bid}:outcome",
                schemas.OUTCOME,
                {},
                validator=validate,
                context=context,
            )
            battle.update(
                status="settling",
                combat_participants=members,
                winner_ids=result["winners"],
                loser_ids=sorted(result["losers"]),
                settlements={},
            )
            work.update(stage="settlement_init", loser_index=0)
            return [
                draft(
                    "BattleOutcomeCommitted",
                    payload={
                        "battle_id": bid,
                        "winner_ids": result["winners"],
                        "loser_ids": result["losers"],
                    },
                    audience=members,
                )
            ]
        if stage == "settlement_init":
            losers = battle["loser_ids"]
            if work["loser_index"] >= len(losers):
                battle["status"] = "closed"
                for pid in battle["members"]:
                    state["pieces"][pid]["battle_id"] = None
                battle["members"] = []
                work["stage"] = "done"
                return [
                    draft(
                        "BattleClosed",
                        payload={"battle_id": bid},
                        audience=battle["combat_participants"],
                    )
                ]
            loser = losers[work["loser_index"]]
            battle["current_loser_id"] = loser
            cid = "settle_" + uuid4().hex
            state["conversations"][cid] = {
                "id": cid,
                "kind": "settlement",
                "status": "active",
                "members": sorted(battle["winner_ids"]) + [loser],
                "initiator": sorted(battle["winner_ids"])[0],
                "message_count": 0,
                "counted_message_ids": [],
                "messages": [],
            }
            work.update(
                stage="settlement_speak",
                conversation_id=cid,
                permit_index=0,
                cycle_speech=False,
                idle_cycles=0,
                terminal_votes={},
                release_votes={},
                release_attempt=0,
            )
            return [
                draft(
                    "SettlementStarted",
                    payload={"battle_id": bid, "conversation_id": cid, "loser_id": loser},
                    audience=state["conversations"][cid]["members"],
                )
            ]
        if stage.startswith("settlement") or stage == "release_vote":
            return await self.settlement_step(state, battle, work)
        if stage == "done":
            work["done"] = True
            return [draft("CombatCursorAdvanced")]
        raise RuleError("CURSOR_INVALID", "The combat cursor is invalid")

    def finish_loser(self, state, battle, work):
        state["conversations"][work["conversation_id"]]["status"] = "closed"
        work.update(stage="settlement_init", loser_index=work["loser_index"] + 1)

    async def settlement_step(self, state, battle, work):
        bid = battle["id"]
        loser = battle["current_loser_id"]
        winners = sorted(battle["winner_ids"])
        conv = state["conversations"][work["conversation_id"]]
        audience = list(conv["members"])
        if work["stage"] == "settlement_transfer":
            offer = state["transfer_offers"][work["offer_id"]]
            result = await self.ask(
                state,
                offer["recipient_id"],
                "accept_transfer",
                f"transfer:{offer['id']}:accept",
                schemas.TRANSFER,
                {
                    "object": state["objects"][offer["object_id"]],
                    "sender_id": offer["sender_id"],
                    "recipient_id": offer["recipient_id"],
                },
            )
            events = resolve_transfer(state, offer, result["accept"])
            work["stage"] = "settlement_speak"
            return events
        if work["stage"] == "release_vote":
            remaining = [pid for pid in winners if pid not in work["release_votes"]]
            if remaining:
                pid = remaining[0]
                result = await self.ask(
                    state,
                    pid,
                    "release_vote",
                    f"battle:{bid}:loser:{loser}:release:{work['release_attempt']}:{pid}",
                    schemas.VOTE,
                    {
                        "battle_id": bid,
                        "loser_id": loser,
                        "winners": winners,
                        "instruction": "Approve or reject this release proposal. Rejecting does not execute the loser.",
                    },
                )
                work["release_votes"][pid] = result["approve"]
                if not result["approve"]:
                    work["stage"] = "settlement_speak"
                    return [
                        draft(
                            "ReleaseVoteCast",
                            pid,
                            {"battle_id": bid, "loser_id": loser, "approve": False},
                            audience,
                        ),
                        draft(
                            "ReleaseProposalRejected",
                            payload={"battle_id": bid, "loser_id": loser},
                            audience=audience,
                        ),
                    ]
                return [
                    draft(
                        "ReleaseVoteCast",
                        pid,
                        {"battle_id": bid, "loser_id": loser, "approve": result["approve"]},
                        audience,
                    )
                ]
            if all(work["release_votes"].values()):
                events = release_loser(state, bid, winners[0], loser, work["release_votes"])
                self.finish_loser(state, battle, work)
                return events
            work["stage"] = "settlement_speak"
            return [
                draft(
                    "ReleaseProposalRejected",
                    payload={"battle_id": bid, "loser_id": loser},
                    audience=audience,
                )
            ]
        if work["stage"] == "settlement_terminal":
            remaining = [pid for pid in winners if pid not in work.setdefault("terminal_votes", {})]
            if not remaining:
                events = release_loser(state, bid, winners[0], loser, work["terminal_votes"])
                self.finish_loser(state, battle, work)
                return events
            pid = remaining[0]
            result = await self.ask(
                state,
                pid,
                "settlement_terminal",
                f"battle:{bid}:loser:{loser}:terminal:{pid}",
                schemas.TERMINAL,
                {
                    "battle_id": bid,
                    "loser_id": loser,
                    "winners": winners,
                    "instruction": "Choose approve_release or execute_loser. Release needs every winner; execute requires one explicit winner.",
                },
            )
            if result["kind"] == "execute_loser":
                events = kill_loser(state, bid, pid, loser)
                self.finish_loser(state, battle, work)
                return events
            work["terminal_votes"][pid] = True
            return [
                draft(
                    "ReleaseVoteCast",
                    pid,
                    {"battle_id": bid, "loser_id": loser, "approve": True, "terminal": True},
                    audience,
                )
            ]
        if work["stage"] != "settlement_speak":
            raise RuleError("CURSOR_INVALID", "The settlement cursor is invalid")
        if (
            conv["message_count"] >= state["limits"]["conversation_max_messages"]
            or work["idle_cycles"] >= 2
        ):
            work["stage"] = "settlement_terminal"
            return [
                draft(
                    "SettlementTerminalStarted",
                    payload={"battle_id": bid, "loser_id": loser},
                    audience=audience,
                )
            ]
        pid = audience[work["permit_index"] % len(audience)]

        def validate(result):
            kind = result["kind"]
            if "loser_id" in result and result["loser_id"] != loser:
                raise RuleError(
                    "SETTLEMENT_WRONG_LOSER", "The request targets a different settlement subject"
                )
            if (
                kind in {"execute_loser", "propose_release", "approve_release"}
                and pid not in winners
            ):
                raise RuleError("SETTLEMENT_UNAUTHORIZED", "Only winners can release or execute")
            if kind == "say" and not result.get("speech", "").strip():
                raise RuleError("EMPTY_SPEECH", "A spoken message cannot be empty")
            if kind == "accept_condition":
                apply_settlement_action(deepcopy(state), bid, pid, result.get("intent", {}))

        result = await self.ask(
            state,
            pid,
            "settlement",
            f"battle:{bid}:loser:{loser}:permit:{work['permit_index']}",
            schemas.SETTLEMENT,
            {
                "battle_id": bid,
                "loser_id": loser,
                "winners": winners,
                "member_ids": audience,
                "message_count": conv["message_count"],
                "remaining_messages": 50 - conv["message_count"],
                "instruction": "Negotiate freely. Only winners can propose release or execute. Accepted conditions affect only your own lawful object/color actions.",
            },
            validator=validate,
        )
        work["permit_index"] += 1
        events = []
        speech = result.get("speech", "").strip()
        if speech:
            mid = f"{conv['id']}:{work['permit_index']}"
            conv["messages"].append(
                {"id": mid, "speaker_id": pid, "speech": speech, "turn": state["turn"]}
            )
            conv["counted_message_ids"].append(mid)
            conv["message_count"] += 1
            work["cycle_speech"] = True
            events.append(
                draft(
                    "SettlementMessage",
                    pid,
                    {
                        "conversation_id": conv["id"],
                        "speaker_id": pid,
                        "speech": speech,
                        "message_id": mid,
                    },
                    audience,
                )
            )
        if work["permit_index"] % len(audience) == 0:
            work["idle_cycles"] = 0 if work["cycle_speech"] else work["idle_cycles"] + 1
            work["cycle_speech"] = False
        kind = result["kind"]
        if kind == "execute_loser":
            events.extend(kill_loser(state, bid, pid, loser))
            self.finish_loser(state, battle, work)
        elif kind in {"propose_release", "approve_release"}:
            work.update(
                stage="release_vote",
                release_votes={pid: True},
                release_attempt=work.get("release_attempt", 0) + 1,
            )
            events.append(
                draft("ReleaseProposed", pid, {"battle_id": bid, "loser_id": loser}, audience)
            )
        elif kind == "leave":
            work["stage"] = "settlement_terminal"
        elif kind == "accept_condition":
            events.extend(apply_settlement_action(state, bid, pid, result["intent"]))
            if result["intent"]["kind"] == "transfer":
                work.update(stage="settlement_transfer", offer_id=events[-1]["payload"]["offer_id"])
        return events or [
            draft(
                "SettlementPermissionPassed", pid, {"battle_id": bid, "loser_id": loser}, audience
            )
        ]
