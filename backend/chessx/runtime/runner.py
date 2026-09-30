"""Single writer, one recoverable transition at a time.

No protocol lives on a Python call stack across commits. A failed/cancelled model
call leaves the same persisted cursor ready for retry; results cannot cross generations.
"""

import asyncio
import copy
import json
import time
from uuid import uuid4

from pydantic import TypeAdapter, ValidationError

from chessx.contexts import build_context
from chessx.contexts.budgets import fit_context, request_input_tokens
from chessx.domain.errors import RuleError
from chessx.domain.models import Intent
from chessx.domain.profile import RULE_MANIFEST_HASH, frozen_rule_manifest
from chessx.llm import FixtureAdapter, ModelError, ProviderAdapter
from chessx.rules.actions import apply_primary, create_transfer_offer, resolve_transfer
from chessx.rules.geometry import begin_turn, legal_move, rest_at_start, visible_positions
from chessx.runtime import schemas
from chessx.runtime.combat import CombatMixin
from chessx.runtime.dialogue import DialogueMixin
from chessx.runtime.movement import MovementMixin
from chessx.runtime.story import StoryMixin
from chessx.storage.store import ConflictError, Store, canonical

INTENTS = TypeAdapter(Intent)


class BudgetError(Exception):
    code = "RUN_BUDGET_EXCEEDED"


class WorldRunner(MovementMixin, DialogueMixin, CombatMixin, StoryMixin):
    def __init__(self, store: Store, settings):
        self.store = store
        self.settings = settings
        self.adapters: dict = {}
        self.tasks: dict[str, asyncio.Task] = {}
        self._operation = None
        self._live_adapter = None

    def adapter(self, state):
        wid = state["world_id"]
        if wid not in self.adapters:
            if state["mode"] == "fixture":
                self.adapters[wid] = FixtureAdapter()
            else:
                if not self.settings.configured:
                    raise ModelError(
                        "MODEL_NOT_CONFIGURED", "Fill the local server configuration first."
                    )
                if self._live_adapter is None:
                    self._live_adapter = ProviderAdapter(
                        base_url=self.settings.base_url,
                        api_key=self.settings.api_key,
                        model=self.settings.model,
                        tokenizer_name=self.settings.tokenizer_name,
                        tokenizer_agreement=self.settings.tokenizer_agreement,
                        timeout=self.settings.timeout_seconds,
                        response_format=self.settings.response_format,
                        provider_profile=self.settings.provider_profile,
                    )
                self.adapters[wid] = self._live_adapter
        return self.adapters[wid]

    def recover(self):
        for state in self.store.list_worlds():
            if state.get("rule_profile") == "demo-v2.1" and "rule_manifest" not in state:
                old_seq, old_gen = state["seq"], state["generation"]
                state["rule_manifest"] = frozen_rule_manifest()
                state["rule_manifest_hash"] = RULE_MANIFEST_HASH
                state = self.store.commit(
                    state,
                    [
                        {
                            "type": "WorldMetadataUpgraded",
                            "payload": {"rule_version": "2.1"},
                            "audience": [],
                        }
                    ],
                    expected_seq=old_seq,
                    expected_generation=old_gen,
                )
            if state["lifecycle"] in {"RUNNING", "STEPPING", "PAUSING"}:
                old_seq, old_gen = state["seq"], state["generation"]
                state["generation"] += 1
                state["lifecycle"] = "PAUSED"
                self.store.commit(
                    state,
                    [{"type": "RestartRecovered", "payload": {}, "audience": []}],
                    expected_seq=old_seq,
                    expected_generation=old_gen,
                )
        self.store.db.execute(
            "UPDATE operations SET status='obsolete',error_code='RESTARTED' WHERE status='pending'"
        )
        self.store.db.execute(
            "UPDATE commands SET status='interrupted',error_code='RESTARTED' WHERE status='accepted'"
        )

    async def close(self):
        for wid, task in list(self.tasks.items()):
            if not task.done():
                self.pause(wid)
        await asyncio.gather(*self.tasks.values(), return_exceptions=True)
        for adapter in self.adapters.values():
            close = getattr(adapter, "close", None)
            if close:
                await close()

    def pause(self, wid):
        state = self.store.get(wid)
        seq, gen = state["seq"], state["generation"]
        state["generation"] += 1
        state["lifecycle"] = "PAUSED"
        self.store.commit(
            state,
            [{"type": "SegmentPaused", "payload": {"reason": "user"}, "audience": []}],
            expected_seq=seq,
            expected_generation=gen,
        )
        task = self.tasks.get(wid)
        if task and not task.done():
            task.cancel()

    def finish_primary(self, state):
        state["cursor"]["actor_index"] += 1
        state["cursor"]["work"] = None

    async def start(self, wid, body):
        state = self.store.get(wid)
        command = self.store.command(wid, body["command_id"], body)
        if command.pop("existing"):
            return command
        kind = body["kind"]
        if kind == "pause":
            self.pause(wid)
            self.store.finish_command(wid, body["command_id"], "completed")
            return {"command_id": body["command_id"], "status": "completed"}
        if any(not task.done() for task in self.tasks.values()):
            self.store.finish_command(wid, body["command_id"], "rejected", "WORLD_BUSY")
            raise RuleError("WORLD_BUSY", "Only one world may execute at a time")
        if kind not in {"run", "resume", "retry", "step_event", "step_turn"}:
            self.store.finish_command(wid, body["command_id"], "rejected", "COMMAND_INVALID")
            raise RuleError("COMMAND_INVALID", "Unsupported command")
        count = body.get("turns", 1)
        budget = body.get("budget", {})
        state["budget"] = {
            "max_calls": budget.get("max_calls", 1000),
            "max_total_tokens": budget.get("max_total_tokens", 2_000_000),
            "max_wall_seconds": budget.get("max_wall_seconds", 1800),
        }
        state["budget_base_calls"] = self.store.call_count(wid)
        state["budget_base_tokens"] = sum(
            state["usage"].get(k, 0) for k in ("input_tokens", "output_tokens", "reasoning_tokens")
        )
        continuing = bool(
            state["segments"] and state["segments"][-1].get("status", "running") != "paused"
        )
        if kind not in {"retry", "step_event"} or not continuing:
            state["target_turn"] = state["completed_turn"] + (1 if kind == "step_turn" else count)
        state["lifecycle"] = "RUNNING"
        state["error"] = None
        if state["phase"] == "BARD" and state.get("cursor", {}).get("story_done"):
            state["phase"] = "START"
            state["cursor"] = {}
        segment = {
            "id": "segment_" + uuid4().hex,
            "from_turn": state["completed_turn"] + 1,
            "target_turn": state["target_turn"],
            "started_seq": state["seq"] + 1,
            "status": "running",
        }
        if continuing:
            segment = state["segments"][-1]
            segment["status"] = "running"
            if state["phase"] != "BARD":
                segment["target_turn"] = state["target_turn"]
        else:
            state["segments"].append(segment)
        self.store.commit(
            state,
            [
                {
                    "type": "SegmentStarted",
                    "payload": {"segment_id": segment["id"], "target_turn": state["target_turn"]},
                    "audience": [],
                }
            ],
            expected_seq=state["seq"],
            expected_generation=state["generation"],
        )
        self.tasks[wid] = asyncio.create_task(
            self._run(wid, body["command_id"], kind == "step_event")
        )
        return command

    async def _run(self, wid, cid, single):
        self._started_at = time.monotonic()
        try:
            while self.store.get(wid)["lifecycle"] == "RUNNING":
                await self.step(wid)
                if single:
                    state = self.store.get(wid)
                    if state["lifecycle"] == "RUNNING":
                        state["lifecycle"] = "PAUSED"
                        self.store.commit(
                            state,
                            [
                                {
                                    "type": "SegmentPaused",
                                    "payload": {"reason": "step"},
                                    "audience": [],
                                }
                            ],
                            expected_seq=state["seq"],
                            expected_generation=state["generation"],
                        )
                    break
                await asyncio.sleep(0)
            state = self.store.get(wid)
            self.store.finish_command(
                wid,
                cid,
                "failed" if state.get("error") else "completed",
                state.get("error", {}).get("code") if state.get("error") else None,
            )
        except asyncio.CancelledError:
            self.store.finish_command(wid, cid, "interrupted")
        except Exception:
            # Never include arbitrary exception text: it can contain HTTP credentials.
            self.store.finish_command(wid, cid, "failed", "INTERNAL_ERROR")
            state = self.store.get(wid)
            state["lifecycle"] = "PAUSED_ERROR"
            state["error"] = {
                "code": "INTERNAL_ERROR",
                "message": "An internal transition failed; the durable cursor is preserved.",
            }
            self.store.commit(
                state,
                [
                    {
                        "type": "TechnicalFailure",
                        "payload": {"code": "INTERNAL_ERROR"},
                        "audience": [],
                    }
                ],
                expected_seq=state["seq"],
                expected_generation=state["generation"],
            )

    async def ask(self, state, pid, stage, slot, schema, protocol, validator=None, context=None):
        wid = state["world_id"]
        context = context or build_context(state, pid, stage, protocol)
        context = copy.deepcopy(context)
        context["stage"] = stage
        key = self.settings.api_key.get_secret_value()
        if key and key in canonical(context):
            raise ModelError(
                "MODEL_SECRET_IN_INPUT",
                "Credentials cannot enter model context or operation records.",
            )
        adapter = self.adapter(state)
        maximum = (
            2048
            if stage == "bard"
            else 1024
            if stage == "memory"
            else 768
            if stage == "battle_narrator"
            else 512
        )
        context = fit_context(
            stage,
            context,
            schema,
            adapter.counter,
            max_output_tokens=maximum,
            model_capacity=self.settings.context_capacity,
        )
        if isinstance(adapter, ProviderAdapter) and not adapter._probed:
            await self.ensure_probe(state, adapter)
        for _ in range(3):
            calls = self.store.call_count(wid)
            total = sum(
                state["usage"].get(k, 0)
                for k in ("input_tokens", "output_tokens", "reasoning_tokens")
            )
            context = fit_context(
                stage,
                context,
                schema,
                adapter.counter,
                max_output_tokens=maximum,
                model_capacity=self.settings.context_capacity,
            )
            estimated = request_input_tokens(stage, context, schema, adapter.counter)
            if (
                calls - state.get("budget_base_calls", 0)
                >= state.get("budget", {}).get("max_calls", 1000)
                or total - state.get("budget_base_tokens", 0) + estimated + maximum + 1000
                > state.get("budget", {}).get("max_total_tokens", 2_000_000)
                or time.monotonic() - getattr(self, "_started_at", time.monotonic())
                > state.get("budget", {}).get("max_wall_seconds", 1800)
            ):
                raise BudgetError()
            attempt = self.store.prepare(wid, slot, state["generation"], context)
            state["usage"]["calls"] = self.store.call_count(wid)
            try:
                result = await adapter.generate(
                    stage,
                    context,
                    schema,
                    max_output_tokens=maximum,
                )
                current = self.store.get(wid)
                if current["generation"] != state["generation"] or current["seq"] != state["seq"]:
                    self.store.fail_operation(wid, slot, attempt, "STALE_GENERATION", True)
                    raise ConflictError()
                if validator:
                    validator(result.content)
                for key in ("input_tokens", "output_tokens", "reasoning_tokens"):
                    state["usage"][key] += getattr(result, key)
                self._operation = (
                    slot,
                    attempt,
                    {
                        "content": result.content,
                        "reasoning": result.reasoning,
                        "input_tokens": result.input_tokens,
                        "output_tokens": result.output_tokens,
                        "reasoning_tokens": result.reasoning_tokens,
                        "latency_ms": result.latency_ms,
                    },
                )
                return result.content
            except asyncio.CancelledError:
                self.store.fail_operation(wid, slot, attempt, "CANCELLED", True)
                raise
            except ConflictError:
                raise
            except (ModelError, RuleError, ValidationError) as error:
                code = getattr(error, "code", "SCHEMA_INVALID")
                self.store.fail_operation(wid, slot, attempt, code)
                context.setdefault("protocol", {})["validation_feedback"] = {
                    "code": code,
                    "instruction": "Return only a legal response to the same stage; no fallback action.",
                }
                if code in {
                    "MODEL_AUTH_FAILED",
                    "MODEL_COT_UNSUPPORTED",
                    "TOKENIZER_UNAVAILABLE",
                    "MODEL_COT_MISSING",
                }:
                    raise
                await asyncio.sleep(0 if state["mode"] == "fixture" else 1)
        raise ModelError(code, "The response did not pass validation after the bounded attempts.")

    async def ensure_probe(self, state, adapter):
        """Probe once per loaded configuration; charge it to the real call budget."""
        wid = state["world_id"]
        if self.store.call_count(wid) - state.get("budget_base_calls", 0) >= state.get(
            "budget", {}
        ).get("max_calls", 1000):
            raise BudgetError()
        slot = "provider:capability_probe"
        attempt = self.store.prepare(
            wid,
            slot,
            state["generation"],
            {"stage": "capability_probe", "instruction": "Verify readiness without role secrets."},
        )
        try:
            capabilities = await adapter.probe()
            current = self.store.get(wid)
            if current["generation"] != state["generation"] or current["seq"] != state["seq"]:
                self.store.fail_operation(wid, slot, attempt, "STALE_GENERATION", True)
                raise ConflictError()
            result = adapter.last_probe_result
            record = {
                "capabilities": capabilities,
                "content": result.content,
                "reasoning": result.reasoning,
                "input_tokens": result.input_tokens,
                "output_tokens": result.output_tokens,
                "reasoning_tokens": result.reasoning_tokens,
            }
            for key in ("input_tokens", "output_tokens", "reasoning_tokens"):
                state["usage"][key] += getattr(result, key)
            self.store.db.execute(
                "UPDATE operations SET status='succeeded',result=? WHERE world_id=? AND slot=? AND attempt=?",
                (json.dumps(record), wid, slot, attempt),
            )
            state["usage"]["calls"] = self.store.call_count(wid)
        except asyncio.CancelledError:
            self.store.fail_operation(wid, slot, attempt, "CANCELLED", True)
            raise
        except ModelError as error:
            self.store.fail_operation(wid, slot, attempt, error.code)
            raise

    async def step(self, wid):
        before = self.store.get(wid)
        state = copy.deepcopy(before)
        self._operation = None
        try:
            events = await self.transition(state)
            self._audiences(before, state, events)
            return self.store.commit(
                state,
                events,
                expected_seq=before["seq"],
                expected_generation=before["generation"],
                operation=self._operation,
            )
        except ConflictError:
            return self.store.get(wid)
        except (RuleError, ModelError, BudgetError, ValidationError) as error:
            current = self.store.get(wid)
            if current["generation"] != before["generation"]:
                return current
            current["lifecycle"] = (
                "PAUSED_BUDGET" if isinstance(error, BudgetError) else "PAUSED_ERROR"
            )
            current["usage"]["calls"] = self.store.call_count(wid)
            current["error"] = {
                "code": getattr(error, "code", "SCHEMA_INVALID"),
                "message": "The current operation could not complete. Check configuration or retry this preserved cursor.",
            }
            return self.store.commit(
                current,
                [
                    {
                        "type": "TechnicalFailure",
                        "payload": {"code": current["error"]["code"]},
                        "audience": [],
                    }
                ],
                expected_seq=current["seq"],
                expected_generation=current["generation"],
            )

    async def transition(self, state):
        phase = state["phase"]
        if phase == "START":
            state["turn"] = state["completed_turn"] + 1
            begin_turn(state)
            state["phase"] = "PRIMARY"
            state["cursor"] = {"actor_index": 0, "work": None}
            return [{"type": "TurnStarted", "payload": {"turn": state["turn"]}, "audience": []}]
        if phase == "PRIMARY":
            return await self.primary_step(state)
        if phase == "COMBAT":
            cursor = state["cursor"]
            index = cursor["battle_index"]
            if index >= len(cursor["battle_queue"]):
                state["phase"] = "ORDINARY"
                state["cursor"] = {
                    "conversation_queue": [
                        cid
                        for cid, c in state["conversations"].items()
                        if c["status"] == "active" and c.get("kind", "ordinary") == "ordinary"
                    ],
                    "conversation_index": 0,
                    "work": None,
                }
                return [{"type": "PhaseChanged", "payload": {"phase": "ORDINARY"}, "audience": []}]
            bid = cursor["battle_queue"][index]
            if state["battles"][bid]["status"] not in {"active", "settling"}:
                cursor["battle_index"] += 1
                return []
            work = cursor.get("work")
            if work is None:
                work = {"battle_id": bid, "stage": "narrate"}
                cursor["work"] = work
            events = await self.combat_step(state, work)
            if work.get("done"):
                cursor["work"] = None
                cursor["battle_index"] += 1
            return events
        if phase == "ORDINARY":
            cursor = state["cursor"]
            index = cursor["conversation_index"]
            if index >= len(cursor["conversation_queue"]):
                state["phase"] = "MEMORY"
                state["cursor"] = {"actor_index": 0}
                return []
            cid = cursor["conversation_queue"][index]
            if cursor.get("work") is None and state["conversations"][cid]["status"] != "active":
                cursor["conversation_index"] += 1
                cursor["work"] = None
                return []
            if cursor.get("work") is None:
                cursor["work"] = {
                    "kind": "ordinary",
                    "conversation_id": cid,
                    "stage": "speak",
                    "member_index": 0,
                }
            events = await self.ordinary_step(state, cid, cursor["work"])
            if cursor["work"].get("done"):
                cursor["conversation_index"] += 1
                cursor["work"] = None
            return events
        if phase == "MEMORY":
            return await self.memory_step(state)
        if phase == "END":
            state["completed_turn"] = state["turn"]
            if state["completed_turn"] >= state["target_turn"]:
                state["segments"][-1]["source_end_seq"] = state["seq"] + 1
                state["phase"] = "BARD"
                state["cursor"] = {"story_done": False}
            else:
                state["phase"] = "START"
                state["cursor"] = {}
            return [{"type": "TurnCompleted", "payload": {"turn": state["turn"]}, "audience": []}]
        if phase == "BARD":
            return await self.bard_step(state)
        raise RuleError("CURSOR_INVALID", "The runtime phase is unknown")

    def validate_action(self, state, pid, result):
        result = INTENTS.validate_python(result).model_dump(exclude_none=True)
        kind = result["kind"]
        if kind == "move":
            legal_move(state, pid, result["target"])
            arrival = result.get("arrival_action")
            if arrival:
                battle = state["battles"].get(arrival["battle_id"])
                if (
                    not battle
                    or battle["status"] != "active"
                    or result["target"] != battle["anchor"]
                ):
                    raise RuleError(
                        "BATTLE_NOT_ACTIVE", "Arrival requires an active known battle anchor"
                    )
        elif kind == "invite":
            target = state["pieces"].get(result["target_id"])
            if (
                rest_at_start(state, pid) > 0
                or not target
                or not target["alive"]
                or target["id"] == pid
                or target["position"] not in visible_positions(state, pid)
            ):
                raise RuleError(
                    "INVITATION_INVALID",
                    "Invitations require another currently visible live piece and an active inviter",
                )
        elif kind == "transfer":
            create_transfer_offer(
                copy.deepcopy(state), pid, result["object_id"], result["target_id"]
            )
        else:
            apply_primary(copy.deepcopy(state), pid, result)

    async def primary_step(self, state):
        cursor = state["cursor"]
        if cursor["actor_index"] >= len(state["actor_order"]):
            state["phase"] = "COMBAT"
            state["cursor"] = {
                "battle_queue": [
                    bid
                    for bid, b in state["battles"].items()
                    if b["status"] == "active" and b["created_turn"] < state["turn"]
                ],
                "battle_index": 0,
                "work": None,
            }
            return []
        pid = state["actor_order"][cursor["actor_index"]]
        piece = state["pieces"][pid]
        if not piece["alive"] or piece["battle_id"] or pid in state.get("locked_this_turn", []):
            self.finish_primary(state)
            return [{"type": "PrimarySkipped", "actor_id": pid, "payload": {}, "audience": []}]
        work = cursor.get("work")
        if work is None:
            protocol = {
                "instruction": "Choose exactly one legal action. Color is only a label. No independent attack, promotion, remote join, object destruction, HP or rewards. During rest, move and invite are forbidden. Pawn orthogonal step or occupied diagonal step; knight jumps without intermediate path.",
                "mode": state["mode"],
            }
            result = await self.ask(
                state,
                pid,
                "action",
                f"turn:{state['turn']}:primary:{pid}",
                schemas.ACTION,
                protocol,
                validator=lambda data: self.validate_action(state, pid, data),
            )
            cursor["work"] = {
                "actor_id": pid,
                "kind": result["kind"],
                "intent": result,
                "stage": "init",
            }
            return [
                {
                    "type": "ActionChosen",
                    "actor_id": pid,
                    "payload": {"kind": result["kind"]},
                    "audience": [pid],
                }
            ]
        if work["kind"] == "move":
            return await self.move_step(state, work)
        if work["kind"] == "invite":
            return await self.invite_step(state, work)
        if work["kind"] == "transfer":
            if work["stage"] == "init":
                offer = create_transfer_offer(
                    state, pid, work["intent"]["object_id"], work["intent"]["target_id"]
                )
                work.update(stage="respond", offer_id=offer["id"])
                return [
                    {
                        "type": "ObjectTransferOffered",
                        "actor_id": pid,
                        "payload": {
                            "offer_id": offer["id"],
                            "object_id": offer["object_id"],
                            "sender_id": pid,
                            "recipient_id": offer["recipient_id"],
                        },
                        "audience": [pid, offer["recipient_id"]],
                    }
                ]
            offer = state["transfer_offers"][work["offer_id"]]
            result = await self.ask(
                state,
                offer["recipient_id"],
                "accept_transfer",
                f"transfer:{offer['id']}:accept",
                schemas.TRANSFER,
                {
                    "object": state["objects"][offer["object_id"]],
                    "sender_id": pid,
                    "recipient_id": offer["recipient_id"],
                },
            )
            events = resolve_transfer(state, offer, result["accept"])
            self.finish_primary(state)
            return events
        events = apply_primary(state, pid, work["intent"])
        self.finish_primary(state)
        return events

    async def memory_step(self, state):
        index = state["cursor"]["actor_index"]
        if index >= len(state["actor_order"]):
            state["phase"] = "END"
            state["cursor"] = {}
            return []
        pid = state["actor_order"][index]
        piece = state["pieces"][pid]
        own = state["private"][pid]
        receipt_count = len(state["receipts"][pid])
        recent = state["receipts"][pid][own.get("memory_watermark", 0) :]
        triggered = any(
            r["channel"]
            in {"DeathCommitted", "BattleClosed", "ConversationClosed", "LoserReleased"}
            for r in recent
        )
        if not piece["alive"] or not (
            state["turn"] % 5 == 0
            or triggered
            or receipt_count - own.get("memory_watermark", 0) > 30
        ):
            state["cursor"]["actor_index"] += 1
            return []

        def validate(result):
            ids = {r["receipt_id"] for r in state["receipts"][pid]}
            if not set(result["source_receipt_ids"]).issubset(ids):
                raise RuleError(
                    "MEMORY_SOURCE_INVALID", "Memory may cite only receipts personally received"
                )
            if result["goal_decision"] == "replace" and result["goal_status"] not in {
                "achieved",
                "impossible",
            }:
                raise RuleError(
                    "GOAL_REPLACEMENT_INVALID",
                    "Replacing goals requires your own achieved or permanently impossible determination",
                )

        result = await self.ask(
            state,
            pid,
            "memory",
            f"turn:{state['turn']}:memory:{pid}",
            schemas.MEMORY,
            {
                "instruction": "Revise only your own remembered account. Preserve disagreements. keep/replace/refine your textual goals; missing information alone is not permanent impossibility. Cite only receipts provided."
            },
            validator=validate,
        )
        own["memory"] = result["memory"]
        own["memory_version"] += 1
        own["memory_watermark"] = receipt_count
        events = [
            {
                "type": "MemoryUpdated",
                "actor_id": pid,
                "payload": {
                    "version": own["memory_version"],
                    "source_receipt_ids": result["source_receipt_ids"],
                },
                "audience": [pid],
            }
        ]
        if result["goal_decision"] != "keep":
            own["goals"] = result["goals_markdown"]
            own["goal_version"] += 1
            events.append(
                {
                    "type": "GoalUpdated",
                    "actor_id": pid,
                    "payload": {
                        "version": own["goal_version"],
                        "decision": result["goal_decision"],
                        "reason_summary": result["reason_summary"],
                    },
                    "audience": [pid],
                }
            )
        state["cursor"]["actor_index"] += 1
        return events

    def _audiences(self, before, state, events):
        for event in events:
            if "audience" in event:
                continue
            if event["type"] in {"MembershipChanged", "ConversationClosed"}:
                cid = event.get("payload", {}).get("conversation_id")
                if cid:
                    event["audience"] = before["conversations"].get(cid, {}).get("members", [])
                    continue
            positions = []
            for key in ("position", "origin", "anchor"):
                pos = event.get("payload", {}).get(key)
                if pos:
                    positions.append(pos)
            actor = event.get("actor_id")
            if actor in state["pieces"]:
                positions.extend(
                    [before["pieces"][actor]["position"], state["pieces"][actor]["position"]]
                )
            audience = []
            for pid, piece in state["pieces"].items():
                if piece["alive"] and (
                    pid == actor or any(pos in visible_positions(state, pid) for pos in positions)
                ):
                    audience.append(pid)
            event["audience"] = audience
