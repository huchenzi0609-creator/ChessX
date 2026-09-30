"""Public API lifecycle checks; entirely offline engineering models."""

import asyncio
import time
from copy import deepcopy

import pytest
from chessx.api.app import create_app
from chessx.config import ROOT, Settings
from chessx.domain.scenarios import create_world, load_scenario
from chessx.runtime.runner import WorldRunner
from chessx.storage import Store
from fastapi.testclient import TestClient


def wait_command(client, wid, command_id):
    for _ in range(2000):
        command = client.get(f"/api/worlds/{wid}/commands/{command_id}").json()
        if command["status"] not in {"accepted", "running"}:
            return client.get(f"/api/worlds/{wid}").json()
        time.sleep(0.002)
    pytest.fail("Local command did not terminate")


def create_sample(client, scenario_id="the-seal"):
    scenario = client.get(f"/api/scenarios/{scenario_id}").json()
    response = client.post("/api/worlds", json={"scenario": scenario, "mode": "fixture"})
    assert response.status_code == 201
    return response.json()["world_id"]


def run(client, wid, cid, kind="run", turns=1, budget=None):
    body = {"command_id": cid, "kind": kind, "turns": turns}
    if budget:
        body["budget"] = budget
    assert client.post(f"/api/worlds/{wid}/commands", json=body).status_code == 202
    return wait_command(client, wid, cid)


def test_five_turn_segment_resume_replay_and_private_archive(tmp_path):
    with TestClient(create_app(Settings(data_dir=tmp_path))) as client:
        wid = create_sample(client)
        first = run(client, wid, "first", turns=5)
        assert first["completed_turn"] == 5
        assert first["lifecycle"] == "PAUSED_SEGMENT"
        assert len(first["stories"]) == 1
        second = run(client, wid, "second", kind="resume", turns=3)
        assert second["completed_turn"] == 8
        assert len(second["stories"]) == 2
        replay = client.get(f"/api/worlds/{wid}/replay?at_seq={first['seq']}").json()
        assert replay["pieces"] == first["pieces"]
        assert replay["completed_turn"] == 5
        raw = client.app.state.store.get(wid)
        assert raw["rule_manifest"]["spec_version"] == "2.1"
        for pid in raw["pieces"]:
            assert raw["private"][pid]["goals"]
            assert (
                raw["private"][pid]["persona"]
                == raw["scenario"]["pieces"][raw["actor_order"].index(pid)]["private_text"]
            )


def test_budget_is_not_silence_or_game_over_and_resume_continues_same_slot(tmp_path):
    with TestClient(create_app(Settings(data_dir=tmp_path))) as client:
        wid = create_sample(client)
        paused = run(
            client,
            wid,
            "limited",
            turns=2,
            budget={"max_calls": 1, "max_total_tokens": 30000, "max_wall_seconds": 30},
        )
        assert paused["lifecycle"] == "PAUSED_BUDGET"
        assert paused["usage"]["calls"] == 1
        cursor = deepcopy(client.app.state.store.get(wid)["cursor"])
        done = run(
            client,
            wid,
            "retry",
            kind="retry",
            budget={"max_calls": 30, "max_total_tokens": 200000, "max_wall_seconds": 30},
        )
        assert done["completed_turn"] == 2
        assert done["lifecycle"] == "PAUSED_SEGMENT"
        assert cursor["actor_index"] == 1  # the first action committed; next actor is still pending
        assert len(done["segments"]) == 1
        succeeded = client.app.state.store.db.execute(
            "SELECT slot,COUNT(*) AS n FROM operations WHERE world_id=? AND status='succeeded' GROUP BY slot",
            (wid,),
        ).fetchall()
        assert all(row["n"] == 1 for row in succeeded)


def test_empty_world_no_model_calls_and_single_event_keeps_segment_identity(tmp_path):
    with TestClient(create_app(Settings(data_dir=tmp_path))) as client:
        wid = create_sample(client, "empty")
        states = [run(client, wid, f"step-{n}", kind="step_event") for n in range(5)]
        assert all(state["usage"]["calls"] == 0 for state in states)
        assert len({state["segments"][0]["id"] for state in states}) == 1
        done = run(client, wid, "continue-empty", kind="resume", turns=3)
        assert done["completed_turn"] == 3
        assert done["lifecycle"] == "PAUSED_SEGMENT"
        assert done["usage"]["calls"] == 0


def test_command_identity_dedupe_and_reset_preserves_original(tmp_path):
    with TestClient(create_app(Settings(data_dir=tmp_path))) as client:
        wid = create_sample(client)
        state = run(client, wid, "stable", turns=1)
        replayed = run(client, wid, "stable", turns=1)
        assert replayed["seq"] == state["seq"]
        changed = client.post(
            f"/api/worlds/{wid}/commands", json={"command_id": "stable", "kind": "run", "turns": 2}
        )
        assert changed.status_code == 409
        reset = client.post(
            f"/api/worlds/{wid}/commands", json={"command_id": "reset", "kind": "reset"}
        ).json()
        assert reset["world_id"] != wid
        assert client.get(f"/api/worlds/{wid}").status_code == 200
        assert client.get(f"/api/worlds/{reset['world_id']}").json()["completed_turn"] == 0


async def test_pending_old_story_can_finish_then_new_turns_without_losing_segment(tmp_path):
    store = Store(tmp_path / "story-recovery.sqlite3")
    scenario, personas = load_scenario(ROOT / "scenarios/the-seal.json")
    state = create_world(scenario, personas, "pending")
    state.update(
        mode="fixture",
        phase="BARD",
        turn=2,
        completed_turn=2,
        lifecycle="PAUSED",
        target_turn=2,
        cursor={"story_done": False},
        segments=[
            {"id": "old", "from_turn": 1, "target_turn": 2, "started_seq": 1, "status": "running"}
        ],
    )
    store.create(state)
    store.commit(
        state,
        [
            {
                "type": "PieceWaited",
                "actor_id": "keeper",
                "payload": {"piece_id": "keeper"},
                "audience": ["keeper"],
            }
        ],
        expected_seq=0,
        expected_generation=0,
    )
    runner = WorldRunner(store, Settings(data_dir=tmp_path))
    await runner.start("pending", {"command_id": "continue", "kind": "resume", "turns": 1})
    await asyncio.wait_for(runner.tasks["pending"], 10)
    result = store.get("pending")
    assert result["completed_turn"] == 3
    assert "old" in result["stories"]
    assert len(result["stories"]) == 2
    await runner.close()
    store.close()


def test_store_enforces_exclusive_process_ownership(tmp_path):
    path = tmp_path / "exclusive.sqlite3"
    first = Store(path)
    try:
        with pytest.raises(RuntimeError, match="ALREADY_OWNED"):
            Store(path)
    finally:
        first.close()
    second = Store(path)
    second.close()


def test_zero_sequence_replay_is_exact_initial_checkpoint(tmp_path):
    with TestClient(create_app(Settings(data_dir=tmp_path))) as client:
        wid = create_sample(client)
        initial = client.get(f"/api/worlds/{wid}/replay?at_seq=0")
        assert initial.status_code == 200
        assert initial.json()["seq"] == 0
        assert initial.json()["lifecycle"] == "CREATED"
        start_pieces = deepcopy(initial.json()["pieces"])
        run(client, wid, "later", turns=2)
        replay = client.get(f"/api/worlds/{wid}/replay?at_seq=0").json()
        assert replay["pieces"] == start_pieces
        assert replay["completed_turn"] == 0


def test_story_regeneration_keeps_world_clock_and_versions(tmp_path):
    with TestClient(create_app(Settings(data_dir=tmp_path))) as client:
        wid = create_sample(client)
        original = run(client, wid, "initial-story", turns=1)
        sid = next(iter(original["stories"]))
        response = client.post(f"/api/worlds/{wid}/segments/{sid}/story")
        assert response.status_code == 202
        changed = wait_command(client, wid, response.json()["command_id"])
        assert changed["completed_turn"] == 1
        assert len(changed["segments"]) == 1
        assert changed["stories"][sid]["revision"] == 1
        raw = client.app.state.store.get(wid)
        assert len(raw["story_versions"][sid]) == 2
        slots = client.app.state.store.db.execute(
            "SELECT slot,COUNT(*) AS n FROM operations WHERE world_id=? AND status='succeeded' GROUP BY slot",
            (wid,),
        ).fetchall()
        assert all(record["n"] == 1 for record in slots)
