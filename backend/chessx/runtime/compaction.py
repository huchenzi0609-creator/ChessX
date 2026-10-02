"""Oldest-first compaction; only successfully covered prefixes advance."""

from copy import deepcopy

from chessx.contexts.budgets import fit_context
from chessx.contexts.memory import NOISE, compact_receipt, entry_context
from chessx.domain.errors import RuleError
from chessx.llm import ModelError
from chessx.llm.feedback import validation_feedback
from chessx.runtime import schemas
from chessx.storage.store import canonical, digest


class CompactionMixin:
    async def compact_before_step(self, state):
        if state["phase"] in {"BARD", "MEMORY", "END", "START"}:
            return None
        for pid in state["actor_order"]:
            if not state["pieces"][pid]["alive"]:
                continue
            receipts = state["receipts"][pid]
            watermark = state["private"][pid].get("compaction_watermark", 0)
            if len(receipts) - watermark > 30:
                return await self.compact_owner(state, pid, len(receipts) - 12)
        return None

    def compaction_input(self, state, pid, watermark, end, *, reflect_goals=False):
        own = state["private"][pid]
        batch = [
            compact_receipt(r)
            for r in state["receipts"][pid][watermark:end]
            if r.get("channel") not in NOISE
        ]
        return {
            "actor_id": pid,
            "stage": "memory_compaction",
            "turn": state["turn"],
            "identity": {"id": pid, "name": state["pieces"][pid]["name"]},
            # A legacy summary is from the *end* of the archive. Seeding its
            # oldest prefix with future recollections confuses chronology.
            "previous_summary": own.get("rolling_summary", ""),
            **({"current_goals": own.get("goals", "")} if reflect_goals else {}),
            "reflect_goals": reflect_goals,
            "source_batch": batch,
            "coverage": {"start_receipt_index": watermark, "end_receipt_index": end},
            "retained_important_facts": [
                entry_context(e)
                for e in own.get("long_term_memory", {}).values()
                if e.get("salience") == "important"
            ][-8:],
            "instruction": (
                "Incrementally summarize ONLY this owner's delivered batch. Retain consequential "
                "news, identities, promises, observations and locations with exact source IDs. "
                "Claims remain attributed and locations dated; never invent facts. Ordinary "
                "clock/pass noise is omitted. Summary replacement never deletes durable facts. "
                "Only a completed scheduled memory pass may reflect goals. Otherwise emit keep, active and empty goal/reason strings; do not reason about goals."
            ),
        }

    async def compact_owner(
        self, state, pid, target_end, *, reflect_goals=False, _retry=0, _feedback=None
    ):
        own = state["private"][pid]
        start = own.get("compaction_watermark", 0)
        if target_end <= start:
            return []
        end, semantic = start, 0
        semantic_limit = min(
            self.settings.memory_batch_size, max(1, own.get("compaction_batch_limit", 32))
        )
        while end < target_end and semantic < semantic_limit:
            semantic += state["receipts"][pid][end].get("channel") not in NOISE
            end += 1
        counter = self.adapter(state).counter
        while True:
            goal_reflection = reflect_goals and end == target_end
            context = self.compaction_input(state, pid, start, end, reflect_goals=goal_reflection)
            schema = deepcopy(schemas.COMPACTION)
            if not goal_reflection:
                for key, value in {
                    "goal_decision": "keep",
                    "goal_status": "active",
                    "goals_markdown": "",
                    "reason_summary": "",
                }.items():
                    schema["properties"][key] = {"const": value}
            if _feedback:
                context["protocol"] = {"validation_feedback": _feedback}
            # Small source batches also reduce reasoning work. Capacity alone
            # is insufficient: a large schema-valid transcript can spend the
            # combined response allowance on analysis before finishing JSON.
            if counter.count(canonical(context["source_batch"])) > 1800 and end > start + 1:
                end = start + max(1, (end - start) // 2)
                continue
            try:
                # source_batch is essential input: fit_context never trims it.
                fit_context(
                    "memory_compaction",
                    context,
                    schema,
                    counter,
                    max_output_tokens=2048,
                    model_capacity=self.settings.context_capacity,
                    input_limit=min(6144, self.settings.character_input_limit),
                )
                break
            except ModelError as error:
                if error.code != "CONTEXT_BUDGET_EXCEEDED" or end <= start + 1:
                    raise
                end = start + max(1, (end - start) // 2)
        if not context["source_batch"]:
            own["compaction_watermark"] = own["memory_watermark"] = end
            return [
                {
                    "type": "MemoryCompactionAdvanced",
                    "actor_id": pid,
                    "payload": {"covered_end": end},
                    "audience": [],
                }
            ]
        sources = {r["receipt_id"]: r for r in context["source_batch"]}

        def validate(result):
            if not goal_reflection and result["goal_decision"] != "keep":
                raise RuleError(
                    "GOAL_REPLACEMENT_INVALID", "History maintenance cannot rewrite goals"
                )
            for addition in result["additions"]:
                ids = addition["source_receipt_ids"]
                if not ids or not set(ids).issubset(sources):
                    raise RuleError(
                        "MEMORY_SOURCE_INVALID", "Use only supplied owner batch sources"
                    )
                if addition["kind"] == "observed_fact" and all(
                    sources[s]["channel"]
                    in {"ConversationMessage", "NegotiationMessage", "SettlementMessage"}
                    for s in ids
                ):
                    raise RuleError(
                        "MEMORY_SOURCE_INVALID",
                        "Spoken claims alone cannot establish an observed fact",
                    )
            if result["goal_decision"] == "replace" and result["goal_status"] not in {
                "achieved",
                "impossible",
            }:
                raise RuleError(
                    "GOAL_REPLACEMENT_INVALID", "Replacement requires achieved or impossible goals"
                )

        try:
            result = await self.ask(
                state,
                pid,
                "memory_compaction",
                f"memory:{pid}:prefix:{start}:{end}",
                schema,
                {},
                context=context,
                validator=validate,
                max_attempts=1,
                max_cot_retries=0,
            )
        except ModelError as error:
            repairable = error.code in {
                "COT_BUDGET_EXCEEDED",
                "MODEL_OUTPUT_INVALID",
                "MEMORY_SOURCE_INVALID",
                "GOAL_REPLACEMENT_INVALID",
            }
            retry_limit = 3 if error.code == "COT_BUDGET_EXCEEDED" else 2
            if not repairable or _retry >= retry_limit:
                raise
            # Retry a smaller *unacknowledged* prefix, with source IDs and slot
            # recomputed. Never truncate a response or advance the old target.
            smaller = (
                start + max(1, (end - start) // 2)
                if error.code == "COT_BUDGET_EXCEEDED"
                or error.details.get("reason") in {"RESPONSE_TRUNCATED", "FORMAL_BUDGET_EXCEEDED"}
                else end
            )
            return await self.compact_owner(
                state,
                pid,
                smaller,
                reflect_goals=reflect_goals,
                _retry=_retry + 1,
                _feedback=validation_feedback(
                    error.code, error.details, 2048, stage="memory_compaction"
                ),
            )
        ledger = own.setdefault("long_term_memory", {})
        if _retry:
            own["compaction_batch_limit"] = min(
                semantic_limit, max(1, len(context["source_batch"]))
            )
        for item in result["additions"]:
            key = "fact_" + digest({"kind": item["kind"], "text": item["text"]})[:16]
            previous = ledger.get(key, {})
            learned = [sources[s].get("turn") for s in item["source_receipt_ids"]]
            ledger[key] = {
                **deepcopy(item),
                "id": key,
                "owner_id": pid,
                "salience": "important"
                if previous.get("salience") == "important"
                else item["salience"],
                "learned_turn": min(
                    [t for t in learned if isinstance(t, int)]
                    + [previous.get("learned_turn", state["turn"])]
                ),
                "source_receipt_ids": list(
                    dict.fromkeys(
                        previous.get("source_receipt_ids", []) + item["source_receipt_ids"]
                    )
                ),
            }
        own["rolling_summary"] = own["memory"] = result["summary"]
        own["memory_version"] += 1
        own["compaction_watermark"] = own["memory_watermark"] = end
        own["last_compaction_turn"] = state["turn"]
        events = [
            {
                "type": "MemoryUpdated",
                "actor_id": pid,
                "payload": {
                    "version": own["memory_version"],
                    "covered_start": start,
                    "covered_end": end,
                    "durable_entries": len(ledger),
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
        return events

    async def memory_step(self, state):
        index = state["cursor"]["actor_index"]
        if index >= len(state["actor_order"]):
            state["phase"], state["cursor"] = "END", {}
            return []
        pid = state["actor_order"][index]
        own = state["private"][pid]
        n, start = len(state["receipts"][pid]), own.get("compaction_watermark", 0)
        pending = state["receipts"][pid][start:]
        important = any(
            r.get("channel")
            in {"DeathCommitted", "BattleClosed", "ConversationClosed", "LoserReleased"}
            for r in pending
        )
        target_end = state["cursor"].get("compaction_target_end")
        if (
            state["pieces"][pid]["alive"]
            and n > start
            and (
                n - start > 30
                or target_end is not None
                or important
                or state["turn"] % 5 == 0
                and own.get("last_compaction_turn") != state["turn"]
            )
        ):
            if target_end is None:
                target_end = n
                state["cursor"]["compaction_target_end"] = n
            events = await self.compact_owner(state, pid, target_end, reflect_goals=True)
            if own.get("compaction_watermark", 0) < target_end:
                return events
            state["cursor"].pop("compaction_target_end", None)
            state["cursor"]["actor_index"] += 1
            return events
        state["cursor"].pop("compaction_target_end", None)
        state["cursor"]["actor_index"] += 1
        return []
