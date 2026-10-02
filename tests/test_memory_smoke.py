"""Validate the opt-in diagnostic end to end without credentials or network."""

import importlib.util
import json
from pathlib import Path

from chessx.config import ROOT, Settings
from chessx.domain.models import Scenario
from chessx.domain.scenarios import create_world
from chessx.llm import FixtureAdapter
from chessx.runtime.runner import WorldRunner
from chessx.storage import Store


async def test_diagnostic_compacts_and_reads_memory_without_modifying_source(
    tmp_path, monkeypatch, capsys
):
    spec = importlib.util.spec_from_file_location("memory_smoke", ROOT / "scripts/memory_smoke.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    scenario = Scenario.model_validate(
        {
            "scenario_id": "smoke",
            "title": "Fixture",
            "board": {"rows": 8, "cols": 8},
            "pieces": [
                {
                    "id": "a",
                    "name": "A",
                    "type": "rook",
                    "color": "青",
                    "position": {"row": 0, "col": 0},
                }
            ],
        }
    )
    state = create_world(scenario, {"a": "SYNTHETIC_PRIVATE_DESIGN"}, "smoke_source")
    state["receipts"]["a"] = [
        {
            "receipt_id": "a:1",
            "channel": "ConversationMessage",
            "payload": {
                "turn": 1,
                "actor_id": "reporter",
                "payload": {"speech": "波吕波斯去世了。", "speaker_id": "reporter"},
            },
        }
    ]
    source = Store(tmp_path / "chessx.sqlite3")
    source.create(state)
    source.close()
    monkeypatch.setattr(
        module.Settings, "load", classmethod(lambda _cls: Settings(data_dir=tmp_path))
    )

    class OfflineRunner(WorldRunner):
        def adapter(self, state):
            return FixtureAdapter()

    monkeypatch.setattr(module, "WorldRunner", OfflineRunner)
    await module.main(owner_id="a", max_calls=3, prefix_receipts=4)
    report = json.loads(capsys.readouterr().out)
    assert report["passed"] and report["compaction_passed"] and report["recall_passed"]
    assert report["source_world_unchanged"] and report["calls_including_probe"] == 2
    assert "SYNTHETIC_PRIVATE_DESIGN" not in json.dumps(report)
    reopened = Store(tmp_path / "chessx.sqlite3")
    try:
        assert reopened.get("smoke_source") == state
    finally:
        reopened.close()
    assert (
        Path(tmp_path) / "memory-spatial-validation-summary.json"
    ).stat().st_mode & 0o777 == 0o600
