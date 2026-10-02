"""Stationary sightings are durable, private and recoverable from legacy poses."""

import sqlite3
from copy import deepcopy

import pytest
from chessx.domain.models import Scenario
from chessx.domain.scenarios import create_world
from chessx.storage import Store
from chessx.storage.store import SPATIAL_MEMORY_SCHEMA_VERSION


def world(world_id="spatial"):
    scenario = Scenario.model_validate(
        {
            "scenario_id": "spatial",
            "title": "Spatial fixture",
            "board": {"rows": 8, "cols": 8},
            "pieces": [
                {
                    "id": "a",
                    "name": "Alice",
                    "type": "rook",
                    "color": "青",
                    "position": {"row": 3, "col": 3},
                },
                {
                    "id": "b",
                    "name": "Bob",
                    "type": "rook",
                    "color": "青",
                    "position": {"row": 3, "col": 4},
                },
                {
                    "id": "c",
                    "name": "NEVER_SEEN_NAME",
                    "type": "rook",
                    "color": "青",
                    "position": {"row": 7, "col": 7},
                },
            ],
        }
    )
    return create_world(scenario, {"a": "A_PRIVATE", "b": "B_PRIVATE", "c": "C_PRIVATE"}, world_id)


def commit(store, state, event_type="CursorAdvanced"):
    return store.commit(
        state,
        [{"type": event_type, "payload": {}, "audience": []}],
        expected_seq=state["seq"],
        expected_generation=state["generation"],
    )


def legacy_history(store, monkeypatch):
    """Produce unchanged old-format checkpoints without retroactively editing hashes."""
    state = world()
    for private in state["private"].values():
        private.pop("spatial_memory", None)
    store.create(state)
    with monkeypatch.context() as patch:
        patch.setattr(Store, "recover_spatial_memory", lambda _store, state: deepcopy(state))
        patch.setattr(
            Store, "_record_spatial_sightings", staticmethod(lambda *_args, **_kwargs: None)
        )
        state["turn"] = 1
        state = commit(store, state, "WorldCreated")
        state["pieces"]["b"]["position"] = {"row": 7, "col": 0}
        state["turn"] = 2
        state = commit(store, state)
        state["turn"] = 50
        state = commit(store, state)
    return state


def test_silent_stationary_person_is_saved_at_commit_and_survives_restart(tmp_path):
    path = tmp_path / "spatial.sqlite3"
    store = Store(path)
    state = world()
    store.create(state)
    state = commit(store, state)
    seen = state["private"]["a"]["spatial_memory"]["seen_people"]
    assert list(seen) == ["b"]
    assert seen["b"] == {
        "piece_id": "b",
        "name": "Bob",
        "position": {"row": 3, "col": 4},
        "status_at_sighting": "alive",
        "last_seen_turn": 0,
        "source": "sighting",
        "source_event_id": "spatial:1",
    }
    assert all(
        "spatial_memory" not in event["payload"] for event in store.events("spatial")["events"]
    )
    assert store.replay("spatial", state["seq"]) == state
    store.close()
    reopened = Store(path)
    try:
        assert reopened.get("spatial")["private"]["a"]["spatial_memory"]["seen_people"] == seen
    finally:
        reopened.close()


def test_hidden_movement_and_death_leave_earlier_sighting_unchanged(tmp_path):
    store = Store(tmp_path / "hidden.sqlite3")
    try:
        state = world()
        store.create(state)
        state = commit(store, state)
        previous = deepcopy(state["private"]["a"]["spatial_memory"]["seen_people"]["b"])
        state["pieces"]["b"]["position"] = {"row": 7, "col": 0}
        state["turn"] = 20
        state = commit(store, state)
        assert state["private"]["a"]["spatial_memory"]["seen_people"]["b"] == previous
        state["pieces"]["b"]["alive"] = False
        state["tombstones"]["tomb_b"] = {
            "id": "tomb_b",
            "piece_id": "b",
            "position": {"row": 7, "col": 0},
        }
        state = commit(store, state)
        assert state["private"]["a"]["spatial_memory"]["seen_people"]["b"] == previous
        assert "c" not in state["private"]["a"]["spatial_memory"]["seen_people"]
        state["pieces"]["a"]["position"] = {"row": 7, "col": 1}
        state = commit(store, state)
        observed_death = state["private"]["a"]["spatial_memory"]["seen_people"]["b"]
        assert observed_death["status_at_sighting"] == "dead"
        assert observed_death["position"] == {"row": 7, "col": 0}
        assert observed_death["name"] == "Bob"
    finally:
        store.close()


def test_tombstone_does_not_disclose_name_of_person_never_seen(tmp_path):
    store = Store(tmp_path / "tomb.sqlite3")
    try:
        state = world()
        store.create(state)
        state["pieces"]["c"]["position"] = {"row": 4, "col": 4}
        state["pieces"]["c"]["alive"] = False
        state["tombstones"]["tomb_c"] = {
            "id": "tomb_c",
            "piece_id": "c",
            "position": {"row": 4, "col": 4},
        }
        state = commit(store, state)
        dead = state["private"]["a"]["spatial_memory"]["seen_people"]["c"]
        assert dead["status_at_sighting"] == "dead" and "name" not in dead
    finally:
        store.close()


def test_same_turn_unchanged_sightings_keep_source_but_new_turn_updates_recency(tmp_path):
    store = Store(tmp_path / "unchanged.sqlite3")
    try:
        state = world()
        store.create(state)
        state = commit(store, state)
        source = state["private"]["a"]["spatial_memory"]["seen_people"]["b"]["source_event_id"]
        state = commit(store, state)
        assert (
            state["private"]["a"]["spatial_memory"]["seen_people"]["b"]["source_event_id"] == source
        )
        state["turn"] = 1
        state = commit(store, state)
        seen = state["private"]["a"]["spatial_memory"]["seen_people"]["b"]
        assert seen["last_seen_turn"] == 1 and seen["source_event_id"] == "spatial:3"
    finally:
        store.close()


def test_legacy_recovery_scans_once_for_all_owners_and_never_refreshes_hidden_positions(
    tmp_path, monkeypatch
):
    store = Store(tmp_path / "legacy.sqlite3")
    try:
        state = legacy_history(store, monkeypatch)
        historical_before = store.replay("spatial", 1)
        second_world = world("different_world")
        second_world["pieces"]["c"]["position"] = {"row": 3, "col": 3}
        store.create(second_world)
        commit(store, second_world)
        before = deepcopy(state)
        queries = []
        store.db.set_trace_callback(queries.append)
        recovered = store.recover_spatial_memory(state)
        store.db.set_trace_callback(None)
        assert sum("FROM events" in query for query in queries) == 1
        assert recovered["private"]["a"]["spatial_memory"]["seen_people"]["b"]["position"] == {
            "row": 3,
            "col": 4,
        }
        assert recovered["private"]["b"]["spatial_memory"]["seen_people"]["a"]["position"] == {
            "row": 3,
            "col": 3,
        }
        assert "c" not in recovered["private"]["a"]["spatial_memory"]["seen_people"]
        assert all(
            private["spatial_memory"]["schema_version"] == SPATIAL_MEMORY_SCHEMA_VERSION
            for private in recovered["private"].values()
        )
        assert state == before and store.get("spatial") == before
        queries = []
        store.db.set_trace_callback(queries.append)
        assert store.recover_spatial_memory(recovered) == recovered
        store.db.set_trace_callback(None)
        assert queries == []
        committed = commit(store, recovered)
        assert store.replay("spatial", committed["seq"]) == committed
        assert store.replay("spatial", 1) == historical_before
        assert "spatial_memory" not in store.replay("spatial", 1)["private"]["a"]
    finally:
        store.close()


def test_old_world_commit_automatically_backfills_and_persists_marker(tmp_path, monkeypatch):
    store = Store(tmp_path / "auto.sqlite3")
    try:
        state = legacy_history(store, monkeypatch)
        state = commit(store, state)
        memory = state["private"]["a"]["spatial_memory"]
        assert memory["schema_version"] == SPATIAL_MEMORY_SCHEMA_VERSION
        assert memory["seen_people"]["b"]["last_seen_turn"] == 1
        assert memory == store.get("spatial")["private"]["a"]["spatial_memory"]
    finally:
        store.close()


def test_failed_commit_rolls_back_sightings_together_with_checkpoint(tmp_path):
    store = Store(tmp_path / "atomic.sqlite3")
    try:
        state = world()
        store.create(state)
        state = commit(store, state)
        previous = deepcopy(state)
        store.db.execute(
            "CREATE TRIGGER fixture_abort BEFORE INSERT ON events BEGIN SELECT RAISE(ABORT,'fixture'); END"
        )
        state["turn"] = 10
        state["pieces"]["b"]["position"] = {"row": 4, "col": 3}
        incoming = deepcopy(state)
        with pytest.raises(sqlite3.IntegrityError):
            commit(store, state)
        assert state == incoming
        assert store.get("spatial") == previous
        assert store.replay("spatial", previous["seq"]) == previous
    finally:
        store.close()


def test_recovering_old_checkpoint_does_not_import_a_future_sighting(tmp_path, monkeypatch):
    store = Store(tmp_path / "past.sqlite3")
    try:
        state = legacy_history(store, monkeypatch)
        past = store.replay("spatial", 1)
        state["pieces"]["c"]["position"] = {"row": 3, "col": 3}
        state = commit(store, state)
        assert "c" in state["private"]["a"]["spatial_memory"]["seen_people"]
        old = store.recover_spatial_memory(past)
        assert "c" not in old["private"]["a"]["spatial_memory"]["seen_people"]
    finally:
        store.close()
