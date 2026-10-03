"""Offline storage contracts: exact replay, migrations, backups and private exports."""

import asyncio
import copy
import io
import json
import sqlite3
import stat
import threading
import zipfile
from concurrent.futures import ThreadPoolExecutor

import pytest
from chessx.api.app import Export, create_app
from chessx.config import ROOT, Settings
from chessx.domain.profile import frozen_rule_manifest, profile_manifest_hash
from chessx.domain.scenarios import create_world, load_scenario
from chessx.runtime.versioning import SAVE_SCHEMA_VERSION, policy_id, policy_is_current
from chessx.storage import Store
from chessx.storage.journal import apply_delta, state_delta
from chessx.storage.store import ConflictError, canonical, digest
from fastapi.testclient import TestClient


def sample(wid="journal"):
    scenario, personas = load_scenario(ROOT / "scenarios/the-seal.json")
    value = create_world(scenario, personas, wid)
    value.update(
        mode="fixture", error=None, scenario=scenario.model_dump(), scenario_hash="fixture"
    )
    return value


def commit(store, state, *, drafts=None):
    return store.commit(
        state,
        drafts or [{"type": "CursorAdvanced", "payload": {}, "audience": []}],
        expected_seq=state["seq"],
        expected_generation=state["generation"],
    )


def test_delta_handles_nested_deletion_lists_and_type_replacement():
    before = {
        "archive": [1, {"message": "old"}],
        "private": {"a": {"keep": 1, "drop": 2}},
        "nullable": None,
    }
    after = {
        "archive": [1, {"message": "new"}, 3],
        "private": {"a": {"keep": 1, "new": [0]}},
        "nullable": {"nested": True},
    }
    change = state_delta(before, after)
    assert apply_delta(before, change) == after
    assert before["private"]["a"]["drop"] == 2
    assert state_delta(after, after) == {"set": [], "delete": [], "list": []}


def test_sparse_journal_replays_every_event_and_bounds_checkpoint_distance(tmp_path):
    store = Store(tmp_path / "journal.sqlite3")
    value = sample()
    value["private"]["keeper"]["persona"] += "PRIVATE_STATIC_ANCHOR" * 2000
    store.create(value)
    expected = {0: copy.deepcopy(value)}
    for turn in range(1, 111):
        value["turn"] = turn
        value = commit(store, value)
        expected[value["seq"]] = copy.deepcopy(value)
    for seq, state in expected.items():
        assert store.replay("journal", seq) == state
    rows = store.db.execute(
        "SELECT seq,storage_kind,length(state) AS bytes FROM events WHERE world_id='journal'"
    ).fetchall()
    assert [row["seq"] for row in rows if row["storage_kind"] == "checkpoint"] == [1, 50, 100]
    assert (
        sum(row["bytes"] for row in rows)
        < sum(len(canonical(state)) for seq, state in expected.items() if seq) // 4
    )
    assert list(store.iter_states("journal")) == list(expected.values())
    store.close()


def test_delta_corruption_is_detected_before_returning_replay(tmp_path):
    store = Store(tmp_path / "journal.sqlite3")
    value = sample()
    store.create(value)
    value = commit(store, value)
    value["turn"] = 1
    value = commit(store, value)
    store.db.execute("UPDATE events SET base_seq=999 WHERE world_id='journal' AND seq=2")
    with pytest.raises(ValueError, match="CHECKPOINT_CORRUPTED"):
        store.replay("journal", 2)
    store.close()


def test_legacy_storage_migration_preserves_frozen_state_and_hashes(tmp_path):
    path = tmp_path / "legacy.sqlite3"
    value = sample("legacy")
    value.pop("execution_policy")
    value.pop("execution_policy_hash")
    value.pop("save_schema_version")
    db = sqlite3.connect(path)
    db.executescript(
        "CREATE TABLE events(world_id TEXT,seq INTEGER,event TEXT,state TEXT,PRIMARY KEY(world_id,seq));CREATE TABLE worlds(id TEXT PRIMARY KEY,seq INTEGER,generation INTEGER,state TEXT);CREATE TABLE initial_states(world_id TEXT PRIMARY KEY,state TEXT,checksum TEXT);"
    )
    db.execute("INSERT INTO worlds VALUES(?,?,?,?)", ("legacy", 0, 0, canonical(value)))
    db.execute(
        "INSERT INTO initial_states VALUES(?,?,?)", ("legacy", canonical(value), digest(value))
    )
    db.commit()
    db.close()
    store = Store(path)
    assert (
        store.db.execute("SELECT version FROM schema_version").fetchone()[0] == SAVE_SCHEMA_VERSION
    )
    assert store.get("legacy") == value
    assert store.replay("legacy", 0) == value
    assert policy_id(store.get("legacy")) == "legacy-unversioned"
    store.close()


def test_online_backup_and_compaction_are_lossless_and_private(tmp_path):
    store = Store(tmp_path / "original.sqlite3")
    value = sample()
    store.create(value)
    for turn in range(5):
        value["turn"] = turn
        value = commit(store, value)
    states = list(store.iter_states("journal"))
    # Emulate an older full-snapshot journal, retaining each event hash.
    for historical in states[1:]:
        store.db.execute(
            "UPDATE events SET state=?,storage_kind='checkpoint',base_seq=NULL WHERE world_id=? AND seq=?",
            (canonical(historical), "journal", historical["seq"]),
        )
    backup = store.backup(tmp_path / "backups")
    path = tmp_path / "backups" / backup["filename"]
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    copied = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    assert json.loads(copied.execute("SELECT state FROM worlds").fetchone()[0]) == value
    copied.close()
    report = store.compact_journal("journal")
    assert report["journal_bytes_after"] < report["journal_bytes_before"]
    assert report["database_space_reclaimed"] is False
    assert list(store.iter_states("journal")) == states
    assert store.get("journal") == value
    store.close()


def test_delete_is_atomic_scoped_and_rejects_pending_work(tmp_path):
    store = Store(tmp_path / "delete.sqlite3")
    one, two = sample("one"), sample("two")
    store.create(one)
    store.create(two)
    one = commit(store, one)
    two = commit(store, two)
    store.command("one", "cmd", {"kind": "fixture"})
    attempt = store.prepare("one", "model", 0, {"stage": "action", "actor_id": "keeper"})
    with pytest.raises(ConflictError, match="WORLD_BUSY"):
        store.delete_world("one")
    assert store.get("one") == one
    store.fail_operation("one", "model", attempt, "CANCELLED", obsolete=True)
    store.delete_world("one")
    for table in ("initial_states", "events", "operations", "commands", "text_versions"):
        assert (
            store.db.execute(f"SELECT COUNT(*) FROM {table} WHERE world_id='one'").fetchone()[0]
            == 0
        )
    assert store.get("two") == two
    with pytest.raises(KeyError):
        store.get("one")
    store.close()


def create_sample(client):
    scenario = client.get("/api/scenarios/the-seal").json()
    response = client.post("/api/worlds", json={"scenario": scenario, "mode": "fixture"})
    assert response.status_code == 201
    return response.json()["world_id"]


def test_api_debug_metadata_and_all_agent_inspection_are_scoped_and_paged(tmp_path):
    with TestClient(create_app(Settings(data_dir=tmp_path))) as client:
        wid = create_sample(client)

        def seed():
            store = client.app.state.store
            for i, stage in enumerate(("action", "bard", "battle_narrative")):
                attempt = store.prepare(
                    wid,
                    f"op{i}",
                    0,
                    {
                        "actor_id": "keeper" if stage == "action" else None,
                        "stage": stage,
                        "private": "HIDDEN_CONTEXT_MARKER",
                    },
                )
                if i < 2:
                    store.fail_operation(wid, f"op{i}", attempt, "CANCELLED")
            store.record_operation_usage(
                wid, "op0", 1, {"input_tokens": 5, "output_tokens": 2, "reasoning_tokens": 1}
            )

        client.portal.call(seed)
        public = client.get(f"/api/worlds/{wid}")
        assert "HIDDEN_CONTEXT_MARKER" not in public.text
        runtime = public.json()["runtime"]
        assert runtime["pending_requests"][0]["stage"] == "battle_narrative"
        assert runtime["budget"]["unknown_usage_requests"] == 2
        assert runtime["budget"]["cost"]["known"] is False
        page = client.get(f"/api/worlds/{wid}/inspection?limit=2").json()
        assert page["has_more"] is True
        assert [row["stage"] for row in page["operations"]] == ["battle_narrative", "bard"]
        rest = client.get(
            f"/api/worlds/{wid}/inspection?before_id={page['next_cursor']}&limit=2"
        ).json()
        assert [row["stage"] for row in rest["operations"]] == ["action"]
        assert client.delete(f"/api/worlds/{wid}").status_code == 409


def test_prepared_export_download_is_single_use_and_cleans_temporary_files(tmp_path):
    with TestClient(create_app(Settings(data_dir=tmp_path))) as client:
        wid = create_sample(client)
        prepared = client.post(
            f"/api/worlds/{wid}/export?download=prepare", json={"view": "research"}
        ).json()
        files = list((tmp_path / "exports").glob("*.zip"))
        assert len(files) == 1 and stat.S_IMODE(files[0].stat().st_mode) == 0o600
        response = client.get(prepared["download_url"])
        assert response.status_code == 200
        with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
            checkpoints = json.loads(archive.read("checkpoints.json"))
            raw = client.app.state.store.get(wid)
            assert checkpoints[-1]["state"] == raw
            assert json.loads(archive.read("manifest.json"))["schema_version"] == 2
        assert not list((tmp_path / "exports").glob("*.zip"))
        assert not list((tmp_path / "exports").glob("*.sqlite3"))
        assert client.get(prepared["download_url"]).status_code == 404


def test_upgrade_is_explicit_backs_up_and_leaves_history_unchanged(tmp_path):
    with TestClient(create_app(Settings(data_dir=tmp_path))) as client:
        wid = create_sample(client)

        def legacy():
            store = client.app.state.store
            value = store.get(wid)
            for key in ("execution_policy", "execution_policy_hash", "save_schema_version"):
                value.pop(key)
            value.update(
                rule_profile="demo-v2.1",
                rule_version="2.1",
                rule_manifest=frozen_rule_manifest("demo-v2.1"),
                rule_manifest_hash=profile_manifest_hash("demo-v2.1"),
            )
            return commit(store, value)

        old = client.portal.call(legacy)
        historical = client.get(f"/api/worlds/{wid}/replay?at_seq={old['seq']}").json()
        preview = client.get(f"/api/worlds/{wid}/upgrade-policy").json()
        assert preview["current_policy"] == "legacy-unversioned"
        assert preview["can_upgrade"] is True
        assert client.app.state.store.get(wid) == old
        response = client.post(
            f"/api/worlds/{wid}/upgrade-policy", json={"expected_seq": old["seq"]}
        )
        assert response.status_code == 200
        assert response.json()["world"]["rule_profile"] == "demo-v2.2"
        assert (tmp_path / "backups" / response.json()["backup"]["filename"]).exists()
        assert client.get(f"/api/worlds/{wid}/replay?at_seq={old['seq']}").json() == historical
        assert client.get(f"/api/worlds/{wid}/upgrade-policy").json()["already_current"] is True


def test_upgrade_refuses_pending_protocol_without_dropping_it(tmp_path):
    with TestClient(create_app(Settings(data_dir=tmp_path))) as client:
        wid = create_sample(client)

        def pending():
            store = client.app.state.store
            value = store.get(wid)
            value["execution_policy_hash"] = "old-policy"
            value["cursor"] = {"work": {"stage": "respond", "kind": "invite"}}
            return commit(store, value)

        before = client.portal.call(pending)
        preview = client.get(f"/api/worlds/{wid}/upgrade-policy").json()
        assert preview["can_upgrade"] is False
        assert "PROTOCOL_PENDING" in {row["code"] for row in preview["blockers"]}
        response = client.post(
            f"/api/worlds/{wid}/upgrade-policy", json={"expected_seq": before["seq"]}
        )
        assert response.status_code == 409
        assert client.app.state.store.get(wid) == before
        assert not (tmp_path / "backups").exists()


def test_policy_body_tampering_is_not_hidden_by_a_matching_saved_hash():
    value = sample()
    assert policy_is_current(value)
    value["execution_policy"]["rule_changes"]["vision_radius"] = 99
    assert not policy_is_current(value)


def test_current_policy_prompt_upgrade_preserves_permissions_and_requires_backup(tmp_path):
    with TestClient(create_app(Settings(data_dir=tmp_path))) as client:
        wid = create_sample(client)

        def old_prompt():
            store = client.app.state.store
            value = store.get(wid)
            value["prompt_hash"] = "old-prompt"
            value["dialogue_permissions"] = {
                "turn": 0,
                "actors": {"keeper": {"speeches": 3, "permissions": 5}},
            }
            return commit(store, value)

        before = client.portal.call(old_prompt)
        preview = client.get(f"/api/worlds/{wid}/upgrade-policy").json()
        assert preview["policy_current"] and preview["prompt_changed"] and preview["can_upgrade"]
        response = client.post(
            f"/api/worlds/{wid}/upgrade-policy", json={"expected_seq": before["seq"]}
        )
        assert response.status_code == 200
        after = client.app.state.store.get(wid)
        assert after["dialogue_permissions"] == before["dialogue_permissions"]
        assert after["execution_history"][-1]["previous"]["prompt_hash"] == "old-prompt"
        assert after["model_config_hash"] == digest(after["execution_config"])


def test_unknown_newer_database_schema_is_not_silently_downgraded(tmp_path):
    path = tmp_path / "future.sqlite3"
    store = Store(path)
    store.db.execute("UPDATE schema_version SET version=999")
    store.close()
    with pytest.raises(RuntimeError, match="SAVE_SCHEMA_UNSUPPORTED"):
        Store(path)
    db = sqlite3.connect(path)
    assert db.execute("SELECT version FROM schema_version").fetchone()[0] == 999
    db.close()


def test_sse_snapshot_never_runs_ahead_of_delivered_page_and_stays_private(tmp_path):
    with TestClient(create_app(Settings(data_dir=tmp_path))) as client:
        wid = create_sample(client)

        def seed():
            store = client.app.state.store
            value = store.get(wid)
            for _ in range(204):
                value = commit(store, value)
            return value

        final = client.portal.call(seed)
        endpoint = next(
            route.endpoint
            for route in client.app.routes
            if getattr(route, "path", "") == "/api/worlds/{wid}/stream"
        )

        class Request:
            headers = {}

            async def is_disconnected(self):
                return False

        async def page():
            response = await endpoint(wid, Request(), 0)
            frames = []
            async for frame in response.body_iterator:
                frames.append(frame)
                if frame.startswith("event: cursor"):
                    break
            await response.body_iterator.aclose()
            return frames

        frames = client.portal.call(page)
        snapshot = next(frame for frame in frames if "event: snapshot" in frame)
        payload = json.loads(snapshot.split("data: ", 1)[1])
        cursor = int(frames[-1].split("data: ", 1)[1])
        assert payload["seq"] == cursor == 200
        assert final["seq"] > cursor
        assert "private" not in payload
        assert payload["runtime"]["pending_requests"] == []


def test_role_receipt_pagination_bounds_network_without_pruning_owner_archive(tmp_path):
    with TestClient(create_app(Settings(data_dir=tmp_path))) as client:
        wid = create_sample(client)

        def seed():
            store = client.app.state.store
            value = store.get(wid)
            value["receipts"]["keeper"] = [
                {"receipt_id": f"keeper:{n}", "payload": {"fixture": n}} for n in range(450)
            ]
            return commit(store, value)

        full = client.portal.call(seed)
        nearest = client.get(f"/api/worlds/{wid}/perspectives/keeper").json()
        assert len(nearest["receipts"]) == 200
        assert nearest["receipt_page"]["start_index"] == 250
        assert nearest["receipt_page"]["has_earlier"] is True
        first = client.get(
            f"/api/worlds/{wid}/perspectives/keeper/receipts?after_receipt=0&limit=150"
        ).json()
        assert first["receipts"] == full["receipts"]["keeper"][:150]
        assert first["receipt_page"]["next_cursor"] == 150
        assert "private" not in first
        assert client.app.state.store.get(wid)["receipts"]["keeper"] == full["receipts"]["keeper"]


def test_compact_snapshots_have_paged_dialogue_and_lazy_public_story_body(tmp_path):
    with TestClient(create_app(Settings(data_dir=tmp_path))) as client:
        wid = create_sample(client)

        def seed():
            store = client.app.state.store
            value = store.get(wid)
            messages = [
                {
                    "id": f"m{n}",
                    "message_id": f"m{n}",
                    "speaker_id": "keeper",
                    "speech": f"VISIBLE_LINE_{n}",
                    "turn": 0,
                }
                for n in range(100)
            ]
            value["conversations"]["history"] = {
                "id": "history",
                "kind": "ordinary",
                "status": "closed",
                "members": [],
                "peak_members": 2,
                "messages": messages,
                "counted_message_ids": [m["id"] for m in messages],
                "message_count": 100,
            }
            value["stories"]["public-story"] = {
                "title": "Story",
                "segment_id": "public-story",
                "revision": 0,
                "source_end_seq": value["seq"],
                "paragraphs": [
                    {
                        "text": "PUBLIC_STORY_BODY",
                        "event_refs": [f"{wid}:1"],
                        "claim_kind": "event_based",
                    }
                ],
            }
            return commit(store, value)

        complete = client.portal.call(seed)
        view = client.get(f"/api/worlds/{wid}").json()
        dialog = view["conversations"]["history"]
        assert len(dialog["messages"]) == 50
        assert dialog["message_page"]["start_index"] == 50
        assert dialog["message_count"] == 100
        assert "counted_message_ids" not in dialog
        older = client.get(
            f"/api/worlds/{wid}/conversations/history/messages?before_index=50&limit=50"
        ).json()
        assert older["messages"] == complete["conversations"]["history"]["messages"][:50]
        assert older["message_page"]["has_earlier"] is False
        assert (
            client.get(f"/api/worlds/{wid}/conversations/history/messages?at_seq=0").status_code
            == 404
        )
        assert "paragraphs" not in view["stories"]["public-story"]
        assert view["stories"]["public-story"]["paragraph_count"] == 1
        assert (
            "PUBLIC_STORY_BODY" in client.get(f"/api/worlds/{wid}/segments/public-story/story").text
        )
        historical = client.get(f"/api/worlds/{wid}/replay?at_seq={complete['seq']}").json()
        assert len(historical["conversations"]["history"]["messages"]) == 100
        assert historical["stories"]["public-story"]["paragraphs"][0]["text"] == "PUBLIC_STORY_BODY"


def test_backup_uses_a_separate_read_connection_instead_of_writer_connection(tmp_path, monkeypatch):
    store = Store(tmp_path / "source.sqlite3")
    store.create(sample())

    class ForbiddenWriter:
        def __getattr__(self, name):
            raise AssertionError("The writer connection must stay on the event loop")

    with monkeypatch.context() as patch:
        patch.setattr(store, "db", ForbiddenWriter())
        with ThreadPoolExecutor(max_workers=1) as pool:
            metadata = pool.submit(store.backup, tmp_path / "backup").result(timeout=5)
    db = sqlite3.connect(tmp_path / "backup" / metadata["filename"])
    assert db.execute("SELECT id FROM worlds").fetchone()[0] == "journal"
    db.close()
    store.close()


def test_export_worker_does_not_block_health_and_concurrent_delete_cleans_artifacts(
    tmp_path, monkeypatch
):
    import chessx.api.app as app_module

    entered, release = threading.Event(), threading.Event()
    original = app_module.build_archive

    def controlled(*args, **kwargs):
        entered.set()
        assert release.wait(5), "Worker did not receive release"
        return original(*args, **kwargs)

    monkeypatch.setattr(app_module, "build_archive", controlled)
    with TestClient(create_app(Settings(data_dir=tmp_path))) as client:
        wid = create_sample(client)
        with ThreadPoolExecutor(max_workers=2) as pool:
            exporting = pool.submit(
                client.post, f"/api/worlds/{wid}/export?download=prepare", json={"view": "research"}
            )
            assert entered.wait(2)
            try:
                health = pool.submit(client.get, "/api/health").result(timeout=1)
                assert health.status_code == 200
                assert client.delete(f"/api/worlds/{wid}").status_code == 200
            finally:
                release.set()
            assert exporting.result(timeout=5).status_code == 404
        assert not list((tmp_path / "exports").glob("*.zip"))
        assert not list((tmp_path / "exports").glob("*.sqlite3"))


def test_cancelled_prepared_export_cleans_files_after_worker_finishes(tmp_path, monkeypatch):
    import chessx.api.app as app_module

    entered, release, finished = threading.Event(), threading.Event(), threading.Event()
    original = app_module.build_archive

    def controlled(*args, **kwargs):
        entered.set()
        assert release.wait(5)
        try:
            return original(*args, **kwargs)
        finally:
            finished.set()

    monkeypatch.setattr(app_module, "build_archive", controlled)
    with TestClient(create_app(Settings(data_dir=tmp_path))) as client:
        wid = create_sample(client)
        endpoint = next(
            route.endpoint
            for route in client.app.routes
            if getattr(route, "path", "") == "/api/worlds/{wid}/export"
        )

        async def cancel():
            task = asyncio.create_task(endpoint(wid, Export(view="research"), "prepare"))
            assert await asyncio.to_thread(entered.wait, 2)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
            release.set()
            assert await asyncio.to_thread(finished.wait, 5)
            for _ in range(100):
                if not list((tmp_path / "exports").glob("*.zip")) and not list(
                    (tmp_path / "exports").glob("*.sqlite3")
                ):
                    return
                await asyncio.sleep(0.01)
            pytest.fail("Cancelled export left temporary files")

        client.portal.call(cancel)


def test_maintenance_refuses_other_world_pending_requests(tmp_path):
    with TestClient(create_app(Settings(data_dir=tmp_path))) as client:
        one, two = create_sample(client), create_sample(client)
        client.portal.call(
            client.app.state.store.prepare,
            two,
            "pending",
            0,
            {"stage": "action", "actor_id": "keeper"},
        )
        assert client.post(f"/api/worlds/{one}/compact-journal").status_code == 409
        preview = client.get(f"/api/worlds/{one}/upgrade-policy").json()
        assert "WORLD_BUSY" in {item["code"] for item in preview["blockers"]}


@pytest.mark.parametrize("change", ["profile", "manifest", "saved_hash"])
def test_rule_drift_cannot_hide_under_a_current_execution_policy(change):
    value = sample()
    if change == "profile":
        value["rule_profile"] = "demo-v2.1"
    elif change == "manifest":
        value["rule_manifest"]["unexpected"] = "changed"
    else:
        value["rule_manifest_hash"] = "changed"
    assert not policy_is_current(value)
