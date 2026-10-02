"""Explicit deterministic engineering fixture; never a live model fallback."""

import json

from .interface import ModelError, ModelResult
from .validation import TokenCounter, validate_output


class FixtureAdapter:
    mode = "engineering_fixture"

    def __init__(self):
        self.counter = TokenCounter("cl100k_base")

    async def probe(self) -> dict:
        return {"engineering_fixture": True, "exposed_reasoning": False, "tokenizer": "cl100k_base"}

    async def generate(
        self, stage: str, context: dict, schema: dict, *, max_output_tokens: int = 512
    ) -> ModelResult:
        protocol = context.get("protocol", {})
        fixture_response = protocol.get("fixture_response")
        if fixture_response is not None:
            content = fixture_response
        else:
            content = self._choose(stage, context)
        reasoning = f"Fixture trace: deterministic engineering response for stage {stage}; this is not AI reasoning."
        raw = json.dumps(content, ensure_ascii=False)
        validated = validate_output(
            raw,
            reasoning,
            schema,
            self.counter,
            None if stage == "bard" else max_output_tokens,
            max_reasoning_tokens=None if stage == "bard" else 1000,
        )
        return ModelResult(
            validated,
            reasoning,
            self.counter.count(json.dumps(context, ensure_ascii=False)),
            self.counter.count(raw),
            self.counter.count(reasoning),
            0,
        )

    def _choose(self, stage: str, context: dict) -> dict:
        protocol = context.get("protocol", {})
        observation = context.get("observation", {})
        own = observation.get("self", context.get("self", {}))
        turn = context.get("turn", 0)
        if stage in {"action", "main_action", "decision"}:
            legal = protocol.get("available_intents", context.get("available_intents", []))
            if isinstance(legal, list) and legal and isinstance(legal[0], dict):
                return legal[turn % len(legal)]
            if own.get("can_move") is False or own.get(
                "rest_at_start", own.get("rest_remaining", 0)
            ):
                return {"kind": "wait"}
            inventory = own.get("inventory", [])
            ground = observation.get("objects", [])
            position = own.get("position", {})
            at_feet = [obj for obj in ground if obj.get("location", {}).get("position") == position]
            if at_feet and not inventory:
                return {"kind": "pickup", "object_id": at_feet[0]["id"]}
            neighbors = [
                piece for piece in observation.get("pieces", []) if piece.get("id") != own.get("id")
            ]
            if neighbors and turn % 3 == 2:
                return {"kind": "invite", "target_id": neighbors[0]["id"]}
            board = observation.get("board", {})
            rows, cols = board.get("rows", 8), board.get("cols", 8)
            row, col = position.get("row", 0), position.get("col", 0)
            if own.get("type") == "knight":
                steps = [(2, 1), (1, 2), (-1, 2), (-2, 1), (-2, -1), (-1, -2), (1, -2), (2, -1)]
            elif own.get("type") == "bishop":
                steps = [(1, 1), (1, -1), (-1, -1), (-1, 1)]
            else:
                steps = [(0, 1), (1, 0), (0, -1), (-1, 0)]
            for dr, dc in steps[turn % len(steps) :] + steps[: turn % len(steps)]:
                if 0 <= row + dr < rows and 0 <= col + dc < cols:
                    return {"kind": "move", "target": {"row": row + dr, "col": col + dc}}
            return {"kind": "wait"}
        if stage in {"ordinary", "conversation", "speech"}:
            return (
                {"kind": "say", "speech": "我愿意先听听你的打算。"}
                if protocol.get("message_count", 0) < 2
                else {"kind": "pass"}
            )
        if stage == "negotiation":
            allowed = protocol.get("allowed_results", [])
            proposal = next(
                (item for item in ["stop", "coexist", "pass"] if item in allowed),
                allowed[0] if allowed else "stop",
            )
            return {"kind": "say", "speech": "我同意暂时停下，避免冲突。", "proposal": proposal}
        if stage in {"invite_response", "invitation"}:
            return {"stay": True}
        if stage == "surrender":
            return {"surrender": False}
        if stage == "battle_narrator":
            return {
                "narrative": "双方重新审视对方的姿态，交锋仍未结束。",
                "public_narrative": "双方的交锋仍在继续，尚无死亡。",
            }
        if stage == "outcome":
            participants = protocol.get("participant_ids", context.get("participant_ids", []))
            surrendered = protocol.get("surrendered", context.get("surrendered", []))
            candidates = [pid for pid in participants if pid not in surrendered]
            if not candidates or len(participants) < 2:
                raise ModelError(
                    "FIXTURE_INPUT_INVALID", "Outcome fixture requires eligible participants."
                )
            return {
                "winners": candidates[:1],
                "losers": [pid for pid in participants if pid != candidates[0]],
            }
        if stage == "settlement" and context.get("actor_id") not in protocol.get("winners", []):
            return {"kind": "pass"}
        if stage in {"settlement", "settlement_terminal"}:
            return {"kind": "approve_release"}
        if stage == "accept_transfer":
            return {"accept": True}
        if stage in {"vote", "governance", "release_vote"}:
            return {"approve": True}
        if stage == "memory":
            return {
                "memory": context.get("memory", ""),
                "goals_markdown": context.get("goals", ""),
                "goal_decision": "keep",
                "goal_status": "active",
                "reason_summary": "保留当前目标与个人记忆。",
                "source_receipt_ids": [
                    r.get("receipt_id", r.get("id"))
                    for r in context.get("receipts", [])
                    if r.get("receipt_id", r.get("id"))
                ],
            }
        if stage == "bard":
            events = context.get("events", [])
            return {
                "title": "本段事件记录（工程样例）",
                "paragraphs": [
                    {
                        "text": "这一段记录了角色的行动和相遇。",
                        "event_refs": [event["id"] for event in events[:3]],
                        "claim_kind": "event_based",
                    }
                ],
            }
        raise ModelError(
            "FIXTURE_STAGE_UNSUPPORTED", "Engineering fixture does not support the requested stage."
        )
