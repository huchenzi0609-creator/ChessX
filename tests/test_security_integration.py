"""Security/recovery checks use synthetic secrets and never live providers."""

import asyncio
import io
import stat
import zipfile
from dataclasses import asdict

import httpx
import pytest
from chessx.api.app import create_app
from chessx.config import Settings
from chessx.domain.models import Scenario
from chessx.domain.scenarios import create_world
from chessx.llm import FixtureAdapter, ModelError, ModelResult, ProviderAdapter
from chessx.runtime.runner import WorldRunner
from chessx.storage import Store
from chessx.storage.store import canonical, digest
from fastapi.testclient import TestClient
from pydantic import SecretStr

PERSONA_A = "PERSONA_PRIVATE_A_8f19"
PERSONA_B = "PERSONA_PRIVATE_B_31ca"
TRACE = "COT_PRIVATE_697c"
KEY = "SYNTHETIC_API_KEY_78ac"


def scenario():
    return {
        "scenario_id": "security",
        "title": "Security fixture",
        "board": {"rows": 8, "cols": 8},
        "pieces": [
            {
                "id": "a",
                "name": "A",
                "type": "rook",
                "color": "red",
                "position": {"row": 0, "col": 0},
                "private_text": PERSONA_A,
            },
            {
                "id": "b",
                "name": "B",
                "type": "rook",
                "color": "blue",
                "position": {"row": 7, "col": 7},
                "private_text": PERSONA_B,
            },
        ],
    }


def bare_world():
    data = scenario()
    state = create_world(
        Scenario.model_validate(data), {"a": PERSONA_A, "b": PERSONA_B}, "world_security"
    )
    state.update(mode="fixture", error=None, scenario=data, scenario_hash=digest(data))
    return state


@pytest.fixture
def local_client(tmp_path):
    settings = Settings(api_key=SecretStr(KEY), data_dir=tmp_path / "data")
    with TestClient(create_app(settings)) as client:
        response = client.post("/api/worlds", json={"scenario": scenario(), "mode": "fixture"})
        assert response.status_code == 201
        wid = response.json()["world_id"]
        yield client, wid, settings


def seed_private_records(client, wid):
    def seed():
        store = client.app.state.store
        state = store.get(wid)
        seq, generation = state["seq"], state["generation"]
        attempt = store.prepare(
            wid, "private_fixture", generation, {"actor_id": "a", "persona": PERSONA_A}
        )
        result = asdict(ModelResult({"kind": "wait"}, TRACE, 1, 1, 1, 0))
        state = store.commit(
            state,
            [
                {
                    "type": "ConversationMessage",
                    "payload": {"speaker_id": "a", "speech": "AUDIENCE_ONLY_A"},
                    "audience": ["a"],
                },
                {"type": "MemoryUpdated", "payload": {"memory": PERSONA_A}, "audience": ["a"]},
            ],
            expected_seq=seq,
            expected_generation=generation,
            operation=("private_fixture", attempt, result),
        )
        return state

    return client.portal.call(seed)


def zip_text(content):
    with zipfile.ZipFile(io.BytesIO(content)) as archive:
        return "\n".join(archive.read(name).decode() for name in archive.namelist())


def test_api_and_export_views_keep_private_fields_scoped(local_client):
    client, wid, settings = local_client
    seed_private_records(client, wid)
    observer = client.get(f"/api/worlds/{wid}").text
    public_events = client.get(f"/api/worlds/{wid}/events").text
    assert all(
        secret not in observer + public_events for secret in [PERSONA_A, PERSONA_B, TRACE, KEY]
    )
    role_a = client.get(f"/api/worlds/{wid}/perspectives/a").text
    role_b = client.get(f"/api/worlds/{wid}/perspectives/b").text
    assert PERSONA_A in role_a and "AUDIENCE_ONLY_A" in role_a
    assert PERSONA_B not in role_a and TRACE not in role_a and KEY not in role_a
    assert PERSONA_B in role_b and "AUDIENCE_ONLY_A" not in role_b and PERSONA_A not in role_b
    observer_export = zip_text(
        client.post(f"/api/worlds/{wid}/export", json={"view": "observer"}).content
    )
    research_export = zip_text(
        client.post(f"/api/worlds/{wid}/export", json={"view": "research"}).content
    )
    assert all(secret not in observer_export for secret in [PERSONA_A, PERSONA_B, TRACE, KEY])
    assert all(secret in research_export for secret in [PERSONA_A, PERSONA_B, TRACE])
    assert KEY not in research_export
    assert KEY not in repr(settings)
    assert KEY not in client.get("/api/health").text


def test_request_validation_does_not_echo_supplied_secret(local_client):
    client, wid, _ = local_client
    sentinel = "MALFORMED_REQUEST_SECRET_399a"
    response = client.post(
        f"/api/worlds/{wid}/commands", json={"command_id": "bad", "kind": sentinel}
    )
    assert response.status_code == 422
    assert sentinel not in response.text
    bad = scenario()
    bad["pieces"][0]["position"]["row"] = sentinel
    response = client.post("/api/scenarios/validate", json=bad)
    assert response.json()["valid"] is False
    assert sentinel not in response.text


def test_accidentally_pasted_server_credential_is_rejected_before_state_creation(local_client):
    client, _, _ = local_client
    bad = scenario()
    bad["pieces"][0]["private_text"] += " " + KEY
    for endpoint, body in [
        ("/api/scenarios/validate", bad),
        ("/api/worlds", {"scenario": bad, "mode": "fixture"}),
    ]:
        response = client.post(endpoint, json=body)
        assert response.status_code == 422
        assert "SECRET_IN_SCENARIO" in response.text
        assert KEY not in response.text
    assert len(client.get("/api/worlds").json()) == 1


def test_local_server_rejects_remote_origin_and_rebinding_host(local_client):
    client, _, _ = local_client
    assert (
        client.get("/api/health", headers={"Origin": "https://attacker.invalid"}).status_code == 403
    )
    assert client.get("/api/health", headers={"Host": "attacker.invalid:8000"}).status_code == 403


def test_model_error_handler_does_not_echo_raw_provider_detail(local_client):
    client, _, _ = local_client

    def rejected_adapter(state):
        raise ModelError("MODEL_AUTH_FAILED", KEY + " RAW_PROVIDER_SECRET")

    original = client.app.state.runner.adapter
    client.app.state.runner.adapter = rejected_adapter
    try:
        response = client.post("/api/worlds", json={"scenario": scenario(), "mode": "live"})
    finally:
        client.app.state.runner.adapter = original
    assert response.status_code == 409
    assert response.json()["code"] == "MODEL_AUTH_FAILED"
    assert KEY not in response.text and "RAW_PROVIDER_SECRET" not in response.text


async def test_first_run_probe_auth_failure_is_charged_and_sanitized(tmp_path):
    store = Store(tmp_path / "probe.sqlite3")
    state = bare_world()
    state.update(
        mode="live",
        phase="PRIMARY",
        lifecycle="RUNNING",
        turn=1,
        cursor={"actor_index": 0, "work": None},
    )
    store.create(state)
    runner = WorldRunner(store, Settings(api_key=SecretStr(KEY), data_dir=tmp_path))
    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(401, json={"message": KEY + " RAW_PROVIDER_SECRET"})

    runner.adapters[state["world_id"]] = ProviderAdapter(
        "https://mock-provider.invalid/v1",
        KEY,
        "mock-model",
        tokenizer_agreement=True,
        transport=httpx.MockTransport(handler),
    )
    result = await runner.step(state["world_id"])
    assert result["lifecycle"] == "PAUSED_ERROR"
    assert result["error"]["code"] == "MODEL_AUTH_FAILED"
    assert len(requests) == store.call_count(state["world_id"]) == 1
    persisted = canonical(result) + canonical(
        [
            dict(row)
            for row in store.db.execute("SELECT context,result,error_code,status FROM operations")
        ]
    )
    assert KEY not in persisted and "RAW_PROVIDER_SECRET" not in persisted
    await runner.close()
    store.close()


async def test_pause_invalidates_late_response_and_preserves_operation_cursor(tmp_path):
    store = Store(tmp_path / "cancel.sqlite3")
    state = bare_world()
    state.update(
        phase="PRIMARY", lifecycle="RUNNING", turn=1, cursor={"actor_index": 0, "work": None}
    )
    store.create(state)
    runner = WorldRunner(store, Settings(data_dir=tmp_path))
    entered, release = asyncio.Event(), asyncio.Event()

    class SlowAdapter(FixtureAdapter):
        async def generate(self, stage, context, schema, *, max_output_tokens=512):
            entered.set()
            await release.wait()
            return ModelResult({"kind": "wait"}, TRACE, 1, 1, 1, 0)

    runner.adapters[state["world_id"]] = SlowAdapter()
    pending = asyncio.create_task(runner.step(state["world_id"]))
    await asyncio.wait_for(entered.wait(), 2)
    runner.pause(state["world_id"])
    paused = store.get(state["world_id"])
    release.set()
    await asyncio.wait_for(pending, 2)
    current = store.get(state["world_id"])
    assert current["generation"] == paused["generation"] == 1
    assert current["cursor"] == paused["cursor"] == {"actor_index": 0, "work": None}
    assert current["seq"] == paused["seq"]
    assert not any(
        event["type"] == "ActionChosen" for event in store.events(state["world_id"])["events"]
    )
    record = store.db.execute("SELECT status FROM operations").fetchone()
    assert record["status"] == "obsolete"
    await runner.close()
    store.close()


def test_restart_recovers_pending_cursor_without_regeneration_and_replay_matches(tmp_path):
    path = tmp_path / "recovery.sqlite3"
    store = Store(path)
    state = bare_world()
    cursor = {
        "actor_index": 0,
        "work": {
            "kind": "transfer",
            "stage": "respond",
            "offer_id": "pending_offer",
            "actor_id": "a",
        },
    }
    state.update(phase="PRIMARY", lifecycle="RUNNING", turn=1, cursor=cursor)
    store.create(state)
    state = store.commit(
        state,
        [{"type": "RecoveryFixturePrepared", "payload": {}, "audience": []}],
        expected_seq=0,
        expected_generation=0,
    )
    store.prepare(state["world_id"], "pending_slot", 0, {"actor_id": "a", "persona": PERSONA_A})
    store.command(state["world_id"], "pending_command", {"kind": "run"})
    previous_seq = state["seq"]
    store.close()
    recovered_store = Store(path)
    runner = WorldRunner(recovered_store, Settings(data_dir=tmp_path))
    runner.recover()
    recovered = recovered_store.get(state["world_id"])
    assert recovered["lifecycle"] == "PAUSED" and recovered["generation"] == 1
    assert recovered["cursor"] == cursor
    assert recovered["seq"] == previous_seq + 1
    assert (
        recovered_store.db.execute("SELECT status FROM operations").fetchone()["status"]
        == "obsolete"
    )
    assert (
        recovered_store.get_command(state["world_id"], "pending_command")["status"] == "interrupted"
    )
    assert digest(recovered_store.replay(state["world_id"], recovered["seq"])) == digest(recovered)
    assert digest(recovered_store.replay(state["world_id"], previous_seq)) == digest(state)
    assert canonical(recovered["receipts"]) == canonical(state["receipts"])
    recovered_store.close()


def test_database_and_sidecars_are_owner_only(tmp_path):
    path = tmp_path / "permissions" / "world.sqlite3"
    store = Store(path)
    store.create(bare_world())
    for candidate in [path, path.with_name(path.name + "-wal"), path.with_name(path.name + "-shm")]:
        if candidate.exists():
            assert stat.S_IMODE(candidate.stat().st_mode) == 0o600
    store.close()
