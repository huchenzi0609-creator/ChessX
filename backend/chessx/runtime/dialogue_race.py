"""Bounded speculative replies: one writer selects, cancels and commits one result.

Per-task operation ContextVars and independent state copies isolate each role.
Cancelled results never become speech; all prepared requests consume call budget.
"""

import asyncio
from copy import deepcopy
from functools import partial

from chessx.contexts import build_context
from chessx.contexts.budgets import fit_context, request_input_tokens
from chessx.llm import ModelError, ProviderAdapter
from chessx.llm.feedback import validation_feedback
from chessx.runtime.conversations import ordinary_permission_context, ordinary_slot
from chessx.storage.store import ConflictError, canonical


class DialogueRaceMixin:
    def ordinary_candidate_context(self, state, cid, pid, schema, protocol):
        """Estimate and execute exactly the same owner-private, policy-bound input."""
        context = build_context(
            state,
            pid,
            "ordinary",
            {**protocol, **ordinary_permission_context(state, pid, state["conversations"][cid])},
        )
        context["execution_contract"] = self.execution_binding(state)
        return fit_context(
            "ordinary",
            context,
            schema,
            self.adapter(state).counter,
            max_output_tokens=512,
            model_capacity=self.settings.context_capacity,
            input_limit=self.settings.character_input_limit,
        )

    async def ask_ordinary_race(self, state, cid, pids, schema, protocol, validator):
        from chessx.runtime.runner import BudgetError

        wid = state["world_id"]
        adapter = self.adapter(state)
        # No request is sent for an oversized owner-private anchor.
        contexts = [
            self.ordinary_candidate_context(state, cid, pid, schema, protocol) for pid in pids
        ]
        if isinstance(adapter, ProviderAdapter) and not adapter._probed:
            await self.ensure_probe(state, adapter)
        base_usage = deepcopy(state["usage"])
        spent = sum(
            base_usage.get(k, 0) for k in ("input_tokens", "output_tokens", "reasoning_tokens")
        ) - state.get("budget_base_tokens", 0)
        available_tokens = state.get("budget", {}).get("max_total_tokens", 2_000_000) - spent
        available_calls = state.get("budget", {}).get("max_calls", 1000) - (
            self.store.call_count(wid) - state.get("budget_base_calls", 0)
        )
        selected, reserved = [], 0
        for pid, context in zip(pids, contexts, strict=True):
            cost = request_input_tokens("ordinary", context, schema, adapter.counter, 512) + 1512
            if len(selected) >= available_calls or reserved + cost > available_tokens:
                break
            selected.append((pid, context, cost))
            reserved += cost
        if not selected:
            raise BudgetError()
        if len(selected) == 1:
            pid, context, _ = selected[0]
            return pid, await self.ask(
                state,
                pid,
                "ordinary",
                ordinary_slot(state, cid, pid),
                schema,
                protocol,
                validator=partial(validator, pid),
                context=context,
            )

        candidates, tasks = [], []
        winner = None
        failure = None
        cot_candidate = None

        async def generate(candidate):
            self._operation = None  # Task-local, never another candidate's output.
            snapshot = candidate["state"]

            def prepared(slot, attempt):
                candidate["prepared"] = (slot, attempt)

            try:
                answer = await self.ask(
                    snapshot,
                    candidate["pid"],
                    "ordinary",
                    candidate["slot"],
                    schema,
                    protocol,
                    validator=partial(validator, candidate["pid"]),
                    context=candidate["context"],
                    max_attempts=1,
                    max_cot_retries=0,
                    on_prepare=prepared,
                )
                candidate["operation"] = self._operation
                candidate["answer"] = answer
                return candidate
            finally:
                candidate["usage"] = snapshot["usage"]

        for pid, context, cost in selected:
            snapshot = deepcopy(state)
            # Each branch receives only its reserved one-attempt allowance.
            snapshot.setdefault("budget", {})["max_total_tokens"] = spent + cost
            candidate = {
                "pid": pid,
                "context": context,
                "state": snapshot,
                "slot": ordinary_slot(state, cid, pid),
            }
            candidates.append(candidate)
            tasks.append(asyncio.create_task(generate(candidate)))
        pending = set(tasks)
        try:
            while pending and winner is None:
                done, pending = await asyncio.wait(pending, return_when=asyncio.FIRST_COMPLETED)
                # Same-tick completions have a stable tie break; just one publishes.
                for task in tasks:
                    if task not in done:
                        continue
                    try:
                        winner = task.result()
                        break
                    except (ModelError, ConflictError, BudgetError) as error:
                        failure = failure or error
                        if (
                            getattr(error, "code", "") == "COT_BUDGET_EXCEEDED"
                            and cot_candidate is None
                        ):
                            cot_candidate = candidates[tasks.index(task)]
                            cot_candidate["cot_error"] = error
                        if isinstance(error, ConflictError) or getattr(error, "code", "") in {
                            "MODEL_AUTH_FAILED",
                            "MODEL_BALANCE_INSUFFICIENT",
                            "MODEL_REQUEST_INVALID",
                            "MODEL_RATE_LIMITED",
                        }:
                            raise
            if winner is None and cot_candidate is None:
                raise failure or ModelError(
                    "DIALOGUE_GENERATION_FAILED", "No candidate produced a valid reply."
                )
        finally:
            for task in tasks:
                if not task.done():
                    task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            discard_reason = "DIALOGUE_SUPERSEDED" if winner else "CONVERSATION_CANCELLED"
            for candidate in candidates:
                if candidate is winner:
                    continue
                operation = candidate.get("operation")
                if operation:
                    slot, attempt, record = operation
                    record = {**record, "discarded": True, "discard_reason": discard_reason}
                    self.store.db.execute(
                        "UPDATE operations SET status='obsolete',error_code=?,result=? WHERE world_id=? AND slot=? AND attempt=?",
                        (discard_reason, canonical(record), wid, slot, attempt),
                    )
                elif candidate.get("prepared"):
                    slot, attempt = candidate["prepared"]
                    self.store.db.execute(
                        "UPDATE operations SET error_code=? WHERE world_id=? AND slot=? AND attempt=? AND status='obsolete' AND error_code='CANCELLED'",
                        (discard_reason, wid, slot, attempt),
                    )
            # Completed discarded outputs are also accounted, without becoming
            # world facts. Token usage of requests cancelled upstream is unknown.
            for key in ("input_tokens", "output_tokens", "reasoning_tokens"):
                state["usage"][key] = base_usage.get(key, 0) + sum(
                    c.get("usage", {}).get(key, base_usage.get(key, 0)) - base_usage.get(key, 0)
                    for c in candidates
                )
            state["usage"]["calls"] = self.store.call_count(wid)
        if winner is None:
            # Both branches finished without publishing. Retry one CoT-failing
            # owner on the unchanged conversation, using the shared remaining
            # budget rather than its original one-attempt reservation.
            candidate = cot_candidate
            context = deepcopy(candidate["context"])
            error = candidate["cot_error"]
            context.setdefault("protocol", {})["validation_feedback"] = validation_feedback(
                error.code, error.details, 512
            )
            answer = await self.ask(
                state,
                candidate["pid"],
                "ordinary",
                candidate["slot"],
                schema,
                protocol,
                validator=partial(validator, candidate["pid"]),
                context=context,
            )
            return candidate["pid"], answer
        self._operation = winner["operation"]
        self._operation[2]["dialogue_parallelism"] = len(selected)
        self._operation[2]["conversation_version"] = protocol.get("conversation_version")
        return winner["pid"], winner["answer"]
