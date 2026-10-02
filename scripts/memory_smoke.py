#!/usr/bin/env python3
"""Bounded, explicitly invoked DeepSeek memory check; never advances the source world.

Reads a local saved world, reconstructs owner-only sightings read-only, and tests
one prefix compaction plus recall speech in an isolated private database. Prints
metrics only. Credentials, prompts, memories, speech and CoT are never printed.
"""

import argparse
import asyncio
import json
import os
import sqlite3
import time
from copy import deepcopy
from uuid import uuid4

from chessx.config import Settings
from chessx.contexts import build_context
from chessx.runtime.dialogue import ORDINARY_SCHEMA
from chessx.runtime.runner import WorldRunner
from chessx.storage import Store
from chessx.storage.store import digest


async def main(world_id=None, owner_id="oedipus", max_calls=3, prefix_receipts=40):
    settings = Settings.load()
    source = sqlite3.connect(f"file:{settings.data_dir / 'chessx.sqlite3'}?mode=ro", uri=True)
    source.row_factory = sqlite3.Row
    if world_id:
        row = source.execute("SELECT state FROM worlds WHERE id=?", (world_id,)).fetchone()
    else:
        row = source.execute("SELECT state FROM worlds ORDER BY rowid DESC LIMIT 1").fetchone()
    if row is None:
        print(json.dumps({"passed": False, "error_code": "SOURCE_WORLD_MISSING"}))
        source.close()
        return
    original = json.loads(row[0])
    original_hash = digest(original)
    if owner_id not in original["private"] or not original["pieces"][owner_id]["alive"]:
        print(json.dumps({"passed": False, "error_code": "SOURCE_OWNER_UNAVAILABLE"}))
        source.close()
        return
    # Use only observe at historical owner poses. This helper has a read-only
    # connection and cannot acquire a writer or alter the source journal.
    history = Store.__new__(Store)
    history.db = source
    state = history.recover_spatial_memory(original)
    wid = "memory_smoke_" + uuid4().hex
    state.update(world_id=wid, seq=0, generation=0, mode="live", lifecycle="PAUSED", error=None)
    state["private"][owner_id]["compaction_watermark"] = 0
    state["budget"] = {"max_calls": max_calls, "max_total_tokens": 20000, "max_wall_seconds": 120}
    state["budget_base_calls"] = state["budget_base_tokens"] = 0
    state["usage"] = {"calls": 0, "input_tokens": 0, "output_tokens": 0, "reasoning_tokens": 0}
    store = Store(settings.data_dir / "memory-spatial-validation.sqlite3")
    store.create(state)
    runner = WorldRunner(store, settings)
    runner._started_at = time.monotonic()
    report = {"passed": False, "compaction_passed": False, "recall_passed": False}
    try:
        events = await runner.compact_owner(
            state, owner_id, min(prefix_receipts, len(state["receipts"][owner_id]))
        )
        state = store.commit(
            state, events, expected_seq=0, expected_generation=0, operation=runner._operation
        )
        report["compaction_passed"] = True
        own = state["private"][owner_id]
        report["covered_receipts"] = own["compaction_watermark"]
        report["durable_entries"] = len(own["long_term_memory"])
        context = build_context(
            state,
            owner_id,
            "ordinary",
            {
                "instruction": "Isolated recall diagnostic. Reply naturally to the latest question using only your own supplied knowledge. It is not a new source of world facts.",
                "latest_message": {"speech": "先前关于波吕波斯去世的消息，你还记得吗？"},
            },
        )
        report["old_news_available"] = any(
            "波吕波斯" in str(item)
            for item in context["memory_recall"]["long_term"]
            + context["memory_recall"]["archive_evidence"]
        )
        if store.call_count(wid) >= max_calls:
            report["recall_skipped_call_limit"] = True
            report["passed"] = report["old_news_available"]
            return
        answer = await runner.ask(
            state, owner_id, "ordinary", "diagnostic:recall", ORDINARY_SCHEMA, {}, context=context
        )
        report["recall_passed"] = True
        report["speech_tokens"] = runner.adapter(state).counter.count(answer.get("speech", ""))
        report["response_kind"] = answer["kind"]
        state = store.commit(
            state,
            [{"type": "MemoryDiagnosticCompleted", "audience": [], "payload": {}}],
            expected_seq=state["seq"],
            expected_generation=0,
            operation=runner._operation,
        )
        report["passed"] = report["old_news_available"]
    except Exception as error:
        report["error_code"] = getattr(error, "code", "MEMORY_VERIFICATION_ABORTED")
    finally:
        report["calls_including_probe"] = store.call_count(wid)
        report["usage"] = deepcopy(state["usage"])
        current = source.execute(
            "SELECT state FROM worlds WHERE id=?", (original["world_id"],)
        ).fetchone()
        report["source_world_unchanged"] = digest(json.loads(current[0])) == original_hash
        path = settings.data_dir / "memory-spatial-validation-summary.json"
        path.write_text(json.dumps(report, ensure_ascii=False, indent=2))
        os.chmod(path, 0o600)
        print(json.dumps(report))
        await runner.close()
        store.close()
        source.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--world-id")
    parser.add_argument("--owner-id", default="oedipus")
    parser.add_argument("--max-calls", type=int, choices=range(1, 4), default=3)
    parser.add_argument("--prefix-receipts", type=int, choices=range(1, 41), default=40)
    args = parser.parse_args()
    asyncio.run(main(args.world_id, args.owner_id, args.max_calls, args.prefix_receipts))
