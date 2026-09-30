#!/usr/bin/env python3
"""Explicit bounded real-provider verification; safe metrics only on stdout.

Requires authorization to send the built-in fictional scenario to the configured
provider. Credentials are loaded only inside the process, never argv or stdout.
"""

import asyncio
import json
from uuid import uuid4

from chessx.config import ROOT, Settings
from chessx.domain.scenarios import create_world, load_scenario
from chessx.runtime.runner import WorldRunner
from chessx.storage import Store
from chessx.storage.store import digest


async def main():
    settings = Settings.load()
    store = Store(settings.data_dir / "live-validation.sqlite3")
    scenario, personas = load_scenario(ROOT / "scenarios/the-seal.json")
    wid = "live_smoke_" + uuid4().hex
    state = create_world(scenario, personas, wid)
    state.update(
        mode="live",
        error=None,
        scenario=scenario.model_dump(),
        scenario_hash=digest(
            {"scenario": scenario.model_dump(), "rule_manifest": state["rule_manifest"]}
        ),
    )
    store.create(state)
    store.commit(
        state,
        [
            {
                "type": "WorldCreated",
                "payload": {"mode": "live", "rule_version": "2.1"},
                "audience": [],
            }
        ],
        expected_seq=0,
        expected_generation=0,
    )
    runner = WorldRunner(store, settings)
    try:
        await runner.start(
            wid,
            {
                "command_id": "bounded-live-smoke",
                "kind": "run",
                "turns": 1,
                "budget": {"max_calls": 10, "max_total_tokens": 40000, "max_wall_seconds": 180},
            },
        )
        await asyncio.wait_for(runner.tasks[wid], 200)
        final = store.get(wid)
        report = {
            "mode": "live",
            "completed_turns": final["completed_turn"],
            "lifecycle": final["lifecycle"],
            "error_code": (final.get("error") or {}).get("code"),
            "calls_including_probe": store.call_count(wid),
            "usage": final["usage"],
            "story_count": len(final["stories"]),
            "passed": final["completed_turn"] == 1 and final["lifecycle"] == "PAUSED_SEGMENT",
        }
        (settings.data_dir / "live-smoke-summary.json").write_text(json.dumps(report, indent=2))
        print(json.dumps(report))
    except Exception:
        print(
            json.dumps(
                {
                    "passed": False,
                    "error_code": "LIVE_VERIFICATION_ABORTED",
                    "calls_including_probe": store.call_count(wid),
                }
            )
        )
    finally:
        await runner.close()
        store.close()


if __name__ == "__main__":
    asyncio.run(main())
