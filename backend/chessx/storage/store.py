"""SQLite journal. Every commit stores an exact recovery cursor and state checkpoint.

Model input/results are private operational records, never public event payloads.
The runner is the only state writer. Transactions do not cross an await boundary.
"""

import copy
import fcntl
import hashlib
import json
import os
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from chessx.runtime.versioning import SAVE_SCHEMA_VERSION
from chessx.storage.journal import apply_delta, state_delta

SPATIAL_MEMORY_SCHEMA_VERSION = 1
CHECKPOINT_INTERVAL = 50


def canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def digest(value: Any) -> str:
    return hashlib.sha256(canonical(value).encode()).hexdigest()


class ConflictError(Exception):
    """An asynchronous result no longer owns its expected generation/state."""


class Store:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.path = path
        self._lock_file = open(path.with_suffix(path.suffix + ".lock"), "a+b")
        os.chmod(self._lock_file.name, 0o600)
        try:
            fcntl.flock(self._lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            self._lock_file.close()
            raise RuntimeError("CHESSX_DATABASE_ALREADY_OWNED: use one backend process") from None
        # App writes remain serialized on the runner's event loop. Disabling the
        # driver thread guard also permits read-only integration inspection.
        self.db = sqlite3.connect(path, isolation_level=None, check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        os.chmod(path, 0o600)
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA synchronous=FULL")
        self.db.execute("PRAGMA foreign_keys=ON")
        self.db.execute("PRAGMA busy_timeout=5000")
        self.db.executescript("""
        CREATE TABLE IF NOT EXISTS schema_version(version INTEGER PRIMARY KEY);
        INSERT INTO schema_version SELECT 1 WHERE NOT EXISTS(SELECT 1 FROM schema_version);
        CREATE TABLE IF NOT EXISTS worlds(id TEXT PRIMARY KEY, seq INTEGER NOT NULL, generation INTEGER NOT NULL, state TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS initial_states(world_id TEXT PRIMARY KEY REFERENCES worlds(id), state TEXT NOT NULL, checksum TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS events(world_id TEXT NOT NULL REFERENCES worlds(id), seq INTEGER NOT NULL, event TEXT NOT NULL, state TEXT NOT NULL, PRIMARY KEY(world_id,seq));
        CREATE TABLE IF NOT EXISTS operations(world_id TEXT NOT NULL, slot TEXT NOT NULL, attempt INTEGER NOT NULL, generation INTEGER NOT NULL, context_hash TEXT NOT NULL, context TEXT NOT NULL, status TEXT NOT NULL, result TEXT, error_code TEXT, PRIMARY KEY(world_id,slot,attempt));
        CREATE TABLE IF NOT EXISTS commands(world_id TEXT NOT NULL, id TEXT NOT NULL, body TEXT NOT NULL, status TEXT NOT NULL, error_code TEXT, PRIMARY KEY(world_id,id));
        CREATE TABLE IF NOT EXISTS text_versions(world_id TEXT NOT NULL, owner TEXT NOT NULL, kind TEXT NOT NULL, version INTEGER NOT NULL, payload TEXT NOT NULL, PRIMARY KEY(world_id,owner,kind,version));
        """)
        self._migrate_journal()

    def _migrate_journal(self) -> None:
        """Idempotent storage-only migration; frozen worlds retain their policy."""
        version = self.db.execute("SELECT MAX(version) FROM schema_version").fetchone()[0]
        if version is not None and version > SAVE_SCHEMA_VERSION:
            self.db.close()
            fcntl.flock(self._lock_file.fileno(), fcntl.LOCK_UN)
            self._lock_file.close()
            raise RuntimeError("CHESSX_SAVE_SCHEMA_UNSUPPORTED")
        columns = {row["name"] for row in self.db.execute("PRAGMA table_info(events)")}
        operation_columns = {
            row["name"] for row in self.db.execute("PRAGMA table_info(operations)")
        }
        self.db.execute("BEGIN IMMEDIATE")
        try:
            if "storage_kind" not in columns:
                self.db.execute(
                    "ALTER TABLE events ADD COLUMN storage_kind TEXT NOT NULL DEFAULT 'checkpoint'"
                )
            if "base_seq" not in columns:
                self.db.execute("ALTER TABLE events ADD COLUMN base_seq INTEGER")
            if "usage" not in operation_columns:
                self.db.execute("ALTER TABLE operations ADD COLUMN usage TEXT")
            self.db.execute("DELETE FROM schema_version")
            self.db.execute("INSERT INTO schema_version VALUES(?)", (SAVE_SCHEMA_VERSION,))
            self.db.execute(
                "CREATE INDEX IF NOT EXISTS operations_world_status ON operations(world_id,status)"
            )
            self.db.execute("COMMIT")
        except BaseException:
            self.db.execute("ROLLBACK")
            raise

    def close(self) -> None:
        self.db.close()
        fcntl.flock(self._lock_file.fileno(), fcntl.LOCK_UN)
        self._lock_file.close()

    def create(self, state: dict) -> None:
        self.db.execute("BEGIN IMMEDIATE")
        try:
            self.db.execute(
                "INSERT INTO worlds VALUES(?,?,?,?)",
                (state["world_id"], 0, state["generation"], canonical(state)),
            )
            self.db.execute(
                "INSERT INTO initial_states VALUES(?,?,?)",
                (state["world_id"], canonical(state), digest(state)),
            )
            self.db.execute("COMMIT")
        except BaseException:
            self.db.execute("ROLLBACK")
            raise

    def get(self, world_id: str) -> dict:
        row = self.db.execute("SELECT state FROM worlds WHERE id=?", (world_id,)).fetchone()
        if row is None:
            raise KeyError(world_id)
        return json.loads(row[0])

    def list_worlds(self) -> list[dict]:
        return [
            json.loads(row[0])
            for row in self.db.execute("SELECT state FROM worlds ORDER BY rowid DESC")
        ]

    def world_summaries(self) -> list[dict]:
        rows = self.db.execute("""SELECT id AS world_id,
            json_extract(state,'$.lifecycle') AS lifecycle,
            json_extract(state,'$.turn') AS turn,
            json_extract(state,'$.completed_turn') AS completed_turn,
            json_extract(state,'$.title') AS title,
            json_extract(state,'$.mode') AS mode,
            json_extract(state,'$.execution_policy.id') AS execution_policy,
            json_extract(state,'$.save_schema_version') AS save_schema_version
            FROM worlds ORDER BY rowid DESC""")
        return [dict(row) for row in rows]

    def pending_count(self, world_id: str) -> int:
        return self.db.execute(
            "SELECT COUNT(*) FROM operations WHERE world_id=? AND status='pending'", (world_id,)
        ).fetchone()[0]

    def pending_metadata(self, world_id: str) -> list[dict]:
        return [
            dict(row)
            for row in self.db.execute(
                """SELECT slot AS slot_id,attempt,status,
            json_extract(context,'$.stage') AS stage,
            json_extract(context,'$.actor_id') AS actor_id
            FROM operations WHERE world_id=? AND status='pending' ORDER BY rowid""",
                (world_id,),
            )
        ]

    def delete_world(self, world_id: str) -> None:
        """All world-owned rows disappear together; preset files are independent."""
        self.db.execute("BEGIN IMMEDIATE")
        try:
            row = self.db.execute(
                "SELECT json_extract(state,'$.lifecycle') FROM worlds WHERE id=?", (world_id,)
            ).fetchone()
            if row is None:
                raise KeyError(world_id)
            if row[0] in {"RUNNING", "STEPPING", "PAUSING"} or self.pending_count(world_id):
                raise ConflictError("WORLD_BUSY")
            for table in ("events", "operations", "commands", "text_versions", "initial_states"):
                self.db.execute(f"DELETE FROM {table} WHERE world_id=?", (world_id,))
            self.db.execute("DELETE FROM worlds WHERE id=?", (world_id,))
            self.db.execute("COMMIT")
        except BaseException:
            self.db.execute("ROLLBACK")
            raise

    def backup(self, directory: Path) -> dict:
        """SQLite's online backup includes WAL commits without copying live files."""
        directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        backup_id = "backup_" + uuid4().hex
        path = directory / (backup_id + ".sqlite3")
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        os.close(fd)
        source, destination = None, None
        succeeded = False
        try:
            destination = sqlite3.connect(path)
            source = sqlite3.connect(self.path.resolve().as_uri() + "?mode=ro", uri=True)
            # This dedicated read connection may run on an I/O worker. The
            # single writer connection never leaves its owning event loop.
            source.execute("BEGIN")
            source.execute("SELECT id FROM worlds LIMIT 1").fetchone()
            source.backup(destination, pages=256)
            succeeded = True
        finally:
            if source is not None:
                source.close()
            if destination is not None:
                destination.close()
            if not succeeded:
                path.unlink(missing_ok=True)
        return {
            "backup_id": backup_id,
            "filename": path.name,
            "created_at": datetime.now(UTC).isoformat(),
            "size_bytes": path.stat().st_size,
            "scope": "database",
            "schema_version": SAVE_SCHEMA_VERSION,
        }

    @staticmethod
    def _record_spatial_sightings(
        state: dict,
        snapshot: dict,
        source_event_id: str | None,
        *,
        owners: list[str] | None = None,
    ) -> None:
        """Persist only what each living owner can observe at this checkpoint.

        Hidden living people and hidden deaths never update someone's memory.
        A tombstone supplies an identity and position, but not a hidden name.
        """
        from chessx.contexts import observe

        for owner in owners if owners is not None else state.get("private", {}):
            if owner not in snapshot.get("pieces", {}):
                continue
            private = state["private"][owner]
            memory = private.setdefault("spatial_memory", {})
            seen = memory.setdefault("seen_people", {})
            observation = observe(snapshot, owner)
            sightings = [
                {
                    "piece_id": piece["id"],
                    "name": piece["name"],
                    "position": piece["position"],
                    "status_at_sighting": "alive",
                }
                for piece in observation["pieces"]
                if piece["id"] != owner
            ]
            for tombstone in observation["tombstones"]:
                who = tombstone["piece_id"]
                sighting = {
                    "piece_id": who,
                    "position": tombstone["position"],
                    "status_at_sighting": "dead",
                }
                if isinstance(seen.get(who, {}).get("name"), str):
                    sighting["name"] = seen[who]["name"]
                sightings.append(sighting)
            for sighting in sightings:
                who = sighting["piece_id"]
                entry = {
                    **sighting,
                    "last_seen_turn": snapshot.get("turn", 0),
                    "source": "sighting",
                    "source_event_id": source_event_id,
                }
                previous = seen.get(who, {})
                unchanged = all(
                    previous.get(key) == entry.get(key)
                    for key in ("position", "name", "last_seen_turn", "status_at_sighting")
                )
                if not unchanged or not previous.get("source_event_id"):
                    seen[who] = copy.deepcopy(entry)

    def recover_spatial_memory(self, state: dict) -> dict:
        """Return a legacy world's owner-scoped map recall; caller commits it.

        Scan this world's checkpoints once, observing all missing owners at each
        historical pose. SQLite extracts only small geometric fields, avoiding
        deserialization of every growing receipt/private archive. Never change
        historical snapshots or update an old sighting from today's global pose.
        """
        from chessx.rules.geometry import inside_board

        recovered = copy.deepcopy(state)
        owners = [
            owner
            for owner, private in recovered.get("private", {}).items()
            if private.get("spatial_memory", {}).get("schema_version")
            != SPATIAL_MEMORY_SCHEMA_VERSION
        ]
        if not owners:
            return recovered
        existing = {}
        for owner in owners:
            memory = recovered["private"][owner].setdefault("spatial_memory", {})
            existing[owner] = copy.deepcopy(memory.get("seen_people", {}))
            memory["seen_people"] = {}

        # All owners share one streamed scan. No model/API operations occur.
        def snapshot_from(row) -> dict:
            return {
                "pieces": json.loads(row["pieces"]),
                "board": json.loads(row["board"]),
                "turn": row["turn"],
                "tombstones": json.loads(row["tombstones"] or "{}"),
                "objects": {},
                "rule_profile": row["rule_profile"],
                "rule_manifest": json.loads(row["rule_manifest"] or "{}"),
            }

        world_id = state["world_id"]
        initial = self.db.execute(
            """SELECT json_extract(state,'$.pieces') AS pieces,
                      json_extract(state,'$.board') AS board,
                      json_extract(state,'$.turn') AS turn,
                      json_extract(state,'$.rule_profile') AS rule_profile,
                      json_extract(state,'$.rule_manifest') AS rule_manifest,
                      json_extract(state,'$.tombstones') AS tombstones
               FROM initial_states WHERE world_id=?""",
            (world_id,),
        ).fetchone()
        if initial is not None:
            self._record_spatial_sightings(recovered, snapshot_from(initial), None, owners=owners)
        for historical in self.iter_states(
            world_id, through_seq=state["seq"], include_initial=False
        ):
            # Existing journals may mix full legacy snapshots and delta rows.
            # Observations use each historical checkpoint's frozen rule profile.
            snapshot = {
                key: historical[key]
                for key in (
                    "pieces",
                    "board",
                    "turn",
                    "tombstones",
                    "rule_profile",
                    "rule_manifest",
                )
                if key in historical
            }
            snapshot["objects"] = {}
            self._record_spatial_sightings(
                recovered,
                snapshot,
                f"{world_id}:{historical['seq']}",
                owners=owners,
            )
        for owner in owners:
            memory = recovered["private"][owner]["spatial_memory"]
            seen = memory["seen_people"]
            # Preserve any valid newer owner record supplied by the caller;
            # reported_locations is a separate field and is never promoted.
            for who, record in existing[owner].items():
                if (
                    not isinstance(record, dict)
                    or record.get("source") != "sighting"
                    or who == owner
                    or who not in recovered.get("pieces", {})
                    or not isinstance(record.get("position"), dict)
                    or not inside_board(recovered, record["position"])
                    or record.get("status_at_sighting") not in {"alive", "dead"}
                ):
                    continue
                source = record.get("source_event_id")
                if source is not None and (
                    not isinstance(source, str) or not source.startswith(f"{world_id}:")
                ):
                    continue
                suffix = source.rsplit(":", 1)[-1] if isinstance(source, str) else ""
                if source is not None and (not suffix.isdecimal() or int(suffix) > state["seq"]):
                    continue
                prior = seen.get(who)
                old_turn, new_turn = (
                    (prior or {}).get("last_seen_turn", -1),
                    record.get("last_seen_turn", -1),
                )
                if type(new_turn) is not int or not 0 <= new_turn <= state.get("turn", 0):
                    continue
                if prior is None or (
                    type(old_turn) is int and type(new_turn) is int and new_turn > old_turn
                ):
                    seen[who] = {
                        key: record[key]
                        for key in (
                            "piece_id",
                            "name",
                            "position",
                            "last_seen_turn",
                            "status_at_sighting",
                            "source",
                            "source_event_id",
                        )
                        if key in record
                    }
            memory["schema_version"] = SPATIAL_MEMORY_SCHEMA_VERSION
        return recovered

    def commit(
        self,
        state: dict,
        drafts: list[dict],
        *,
        expected_seq: int,
        expected_generation: int,
        operation: tuple[str, int, dict] | None = None,
    ) -> dict:
        from chessx.contexts import project_event
        from chessx.rules.invariants import assert_invariants

        old = self.get(state["world_id"])
        if old["seq"] != expected_seq or old["generation"] != expected_generation:
            raise ConflictError()
        state = self.recover_spatial_memory(state)
        drafts = drafts or [{"type": "CursorAdvanced", "payload": {}, "audience": []}]
        assert_invariants(state)
        prior_hash = digest(old)
        records = []
        previous = old
        for draft in drafts:
            state["seq"] += 1
            seq = state["seq"]
            event = {
                "event_version": "2.1",
                "world_id": state["world_id"],
                "seq": seq,
                "event_id": f"{state['world_id']}:{seq}",
                "turn": state["turn"],
                "phase": state["phase"],
                "actor_id": draft.get("actor_id"),
                "type": draft["type"],
                "payload": draft.get("payload", {}),
                "audience": draft.get("audience", []),
                "previous_state_hash": prior_hash,
            }
            if event["type"] == "DeathCommitted":
                tombstone_id = event["payload"].get("tombstone_id")
                if tombstone_id in state["tombstones"]:
                    state["tombstones"][tombstone_id]["death_event_id"] = event["event_id"]
            for pid, piece in state["pieces"].items():
                if not piece["alive"] and event["type"] not in {"DeathCommitted", "PieceKilled"}:
                    continue
                projected = project_event(state, event, pid)
                if projected is not None:
                    receipt = {
                        "receipt_id": f"{pid}:{seq}",
                        "source_event_id": event["event_id"],
                        "delivered_seq": seq,
                        "channel": event["type"],
                        "payload": projected,
                    }
                    state["receipts"][pid].append(receipt)
            # Include map recall in the same durable checkpoint and hash as the
            # event. Observations use only local visibility, even for silent,
            # stationary people who have produced no movement receipt.
            self._record_spatial_sightings(state, state, event["event_id"])
            event["resulting_state_hash"] = digest(state)
            prior_hash = event["resulting_state_hash"]
            checkpoint = seq == 1 or seq % CHECKPOINT_INTERVAL == 0
            stored = state if checkpoint else state_delta(previous, state)
            records.append(
                (
                    seq,
                    canonical(event),
                    canonical(stored),
                    "checkpoint" if checkpoint else "delta",
                    None if checkpoint else previous["seq"],
                )
            )
            previous = copy.deepcopy(state)
        self.db.execute("BEGIN IMMEDIATE")
        try:
            row = self.db.execute(
                "SELECT seq,generation FROM worlds WHERE id=?", (state["world_id"],)
            ).fetchone()
            if row is None or tuple(row) != (expected_seq, expected_generation):
                raise ConflictError()
            self.db.execute(
                "UPDATE worlds SET seq=?,generation=?,state=? WHERE id=?",
                (state["seq"], state["generation"], canonical(state), state["world_id"]),
            )
            self.db.executemany(
                "INSERT INTO events(world_id,seq,event,state,storage_kind,base_seq) VALUES(?,?,?,?,?,?)",
                [(state["world_id"], *r) for r in records],
            )
            if operation:
                slot, attempt, result = operation
                self.db.execute(
                    "UPDATE operations SET status='succeeded',result=? WHERE world_id=? AND slot=? AND attempt=?",
                    (canonical(result), state["world_id"], slot, attempt),
                )
            for owner, private in state["private"].items():
                for kind in ("memory", "goals"):
                    version = private.get(
                        "memory_version" if kind == "memory" else "goal_version", 0
                    )
                    self.db.execute(
                        "INSERT OR IGNORE INTO text_versions VALUES(?,?,?,?,?)",
                        (
                            state["world_id"],
                            owner,
                            kind,
                            version,
                            canonical({"markdown": private.get(kind, "")}),
                        ),
                    )
            self.db.execute("COMMIT")
        except BaseException:
            self.db.execute("ROLLBACK")
            raise
        return state

    def events(self, world_id: str, after: int = 0, limit: int = 200) -> dict:
        rows = self.db.execute(
            "SELECT event FROM events WHERE world_id=? AND seq>? ORDER BY seq LIMIT ?",
            (world_id, after, limit + 1),
        ).fetchall()
        events = [json.loads(row[0]) for row in rows[:limit]]
        return {
            "events": events,
            "next_cursor": events[-1]["seq"] if events else after,
            "has_more": len(rows) > limit,
        }

    def replay(self, world_id: str, seq: int) -> dict:
        if seq == 0:
            initial = self.db.execute(
                "SELECT state,checksum FROM initial_states WHERE world_id=?", (world_id,)
            ).fetchone()
            if initial:
                state = json.loads(initial["state"])
                if digest(state) != initial["checksum"]:
                    raise ValueError("CHECKPOINT_CORRUPTED")
                return state
            # Early development journals predate the explicit initial table.
            first = self.db.execute(
                "SELECT event,state FROM events WHERE world_id=? ORDER BY seq LIMIT 1", (world_id,)
            ).fetchone()
            if first and json.loads(first["event"])["type"] == "WorldCreated":
                state = json.loads(first["state"])
                state["seq"] = 0
                return state
            raise KeyError(seq)
        row = self.db.execute(
            "SELECT seq,state,event,storage_kind,base_seq FROM events WHERE world_id=? AND seq<=? AND storage_kind='checkpoint' ORDER BY seq DESC LIMIT 1",
            (world_id, seq),
        ).fetchone()
        if row is None:
            state = self.replay(world_id, 0)
        else:
            state = self._restore_row(None, row)
        for change in self.db.execute(
            "SELECT seq,state,event,storage_kind,base_seq FROM events WHERE world_id=? AND seq>? AND seq<=? ORDER BY seq",
            (world_id, state["seq"], seq),
        ):
            state = self._restore_row(state, change)
        if state["seq"] == 0:
            raise KeyError(seq)
        return state

    @staticmethod
    def _restore_row(previous: dict | None, row) -> dict:
        event = json.loads(row["event"])
        if row["storage_kind"] == "checkpoint":
            state = json.loads(row["state"])
        elif row["storage_kind"] == "delta":
            if (
                previous is None
                or row["base_seq"] != previous["seq"]
                or row["seq"] != previous["seq"] + 1
                or digest(previous) != event["previous_state_hash"]
            ):
                raise ValueError("CHECKPOINT_CORRUPTED")
            try:
                state = apply_delta(previous, json.loads(row["state"]))
            except (KeyError, IndexError, TypeError):
                raise ValueError("CHECKPOINT_CORRUPTED") from None
        else:
            raise ValueError("CHECKPOINT_FORMAT_UNSUPPORTED")
        if digest(state) != event["resulting_state_hash"]:
            raise ValueError("CHECKPOINT_CORRUPTED")
        return state

    def iter_states(
        self, world_id: str, *, through_seq: int | None = None, include_initial: bool = True
    ):
        """Stream one reconstructed exact event snapshot at a time."""
        state = self.replay(world_id, 0)
        if include_initial:
            yield state
        ceiling = (
            through_seq
            if through_seq is not None
            else self.db.execute("SELECT seq FROM worlds WHERE id=?", (world_id,)).fetchone()[0]
        )
        for row in self.db.execute(
            "SELECT seq,state,event,storage_kind,base_seq FROM events WHERE world_id=? AND seq<=? ORDER BY seq",
            (world_id, ceiling),
        ):
            state = self._restore_row(state, row)
            yield state

    def compact_journal(self, world_id: str) -> dict:
        """Explicit lossless rewrite after an external backup; never VACUUM live DB."""
        before = self.db.execute(
            "SELECT COALESCE(SUM(length(state)),0) FROM events WHERE world_id=?", (world_id,)
        ).fetchone()[0]
        # A temporary SQL table avoids buffering a long archive in Python memory.
        self.db.execute("BEGIN IMMEDIATE")
        try:
            self.db.execute(
                "CREATE TEMP TABLE compacted(seq INTEGER PRIMARY KEY,state TEXT,storage_kind TEXT,base_seq INTEGER)"
            )
            previous = self.replay(world_id, 0)
            for state in self.iter_states(world_id, include_initial=False):
                checkpoint = state["seq"] == 1 or state["seq"] % CHECKPOINT_INTERVAL == 0
                stored = state if checkpoint else state_delta(previous, state)
                self.db.execute(
                    "INSERT INTO compacted VALUES(?,?,?,?)",
                    (
                        state["seq"],
                        canonical(stored),
                        "checkpoint" if checkpoint else "delta",
                        None if checkpoint else previous["seq"],
                    ),
                )
                previous = state
            self.db.execute(
                """UPDATE events SET
                state=(SELECT state FROM compacted WHERE seq=events.seq),
                storage_kind=(SELECT storage_kind FROM compacted WHERE seq=events.seq),
                base_seq=(SELECT base_seq FROM compacted WHERE seq=events.seq)
                WHERE world_id=?""",
                (world_id,),
            )
            self.db.execute("DROP TABLE compacted")
            self.db.execute("COMMIT")
        except BaseException:
            self.db.execute("ROLLBACK")
            raise
        after = self.db.execute(
            "SELECT COALESCE(SUM(length(state)),0) FROM events WHERE world_id=?", (world_id,)
        ).fetchone()[0]
        return {
            "world_id": world_id,
            "compacted": True,
            "journal_bytes_before": before,
            "journal_bytes_after": after,
            "database_space_reclaimed": False,
        }

    def prepare(self, world_id: str, slot: str, generation: int, context: dict) -> int:
        attempt = self.db.execute(
            "SELECT COALESCE(MAX(attempt),0)+1 FROM operations WHERE world_id=? AND slot=?",
            (world_id, slot),
        ).fetchone()[0]
        self.db.execute(
            "INSERT INTO operations(world_id,slot,attempt,generation,context_hash,context,status,result,error_code) VALUES(?,?,?,?,?,?,'pending',NULL,NULL)",
            (world_id, slot, attempt, generation, digest(context), canonical(context)),
        )
        return attempt

    def record_operation_usage(self, world_id: str, slot: str, attempt: int, usage: dict) -> None:
        """Record returned counters even if a later schema/COT check rejects output."""
        counters = {
            key: max(0, int(usage[key]))
            for key in ("input_tokens", "output_tokens", "reasoning_tokens")
            if key in usage
        }
        self.db.execute(
            "UPDATE operations SET usage=? WHERE world_id=? AND slot=? AND attempt=?",
            (canonical(counters), world_id, slot, attempt),
        )

    def unknown_usage_count(self, world_id: str) -> int:
        return self.db.execute(
            """SELECT COUNT(*) FROM operations WHERE world_id=?
            AND usage IS NULL AND (result IS NULL OR json_type(result,'$.input_tokens') IS NULL)""",
            (world_id,),
        ).fetchone()[0]

    def fail_operation(
        self,
        world_id: str,
        slot: str,
        attempt: int,
        code: str,
        obsolete: bool = False,
        *,
        details: dict | None = None,
    ) -> None:
        self.db.execute(
            "UPDATE operations SET status=?,error_code=?,result=? WHERE world_id=? AND slot=? AND attempt=?",
            (
                "obsolete" if obsolete else "failed",
                code,
                canonical({"validation": details}) if details else None,
                world_id,
                slot,
                attempt,
            ),
        )

    def operation_records(self, world_id: str, owner: str) -> list[dict]:
        return self.operation_page(world_id, actor_id=owner, limit=100)["operations"]

    def operation_page(
        self,
        world_id: str,
        *,
        actor_id: str | None = None,
        stage: str | None = None,
        before_id: int | None = None,
        limit: int = 50,
    ) -> dict:
        clauses, parameters = ["world_id=?"], [world_id]
        if actor_id is not None:
            clauses.append("json_extract(context,'$.actor_id')=?")
            parameters.append(actor_id)
        if stage is not None:
            clauses.append("json_extract(context,'$.stage')=?")
            parameters.append(stage)
        if before_id is not None:
            clauses.append("rowid<?")
            parameters.append(before_id)
        parameters.append(limit + 1)
        rows = self.db.execute(
            "SELECT rowid AS record_id,slot,attempt,status,context,result,error_code FROM operations WHERE "
            + " AND ".join(clauses)
            + " ORDER BY rowid DESC LIMIT ?",
            parameters,
        ).fetchall()
        result = []
        for row in rows[:limit]:
            context = json.loads(row["context"])
            result.append(
                {
                    "record_id": row["record_id"],
                    "slot_id": row["slot"],
                    "attempt": row["attempt"],
                    "status": row["status"],
                    "actor_id": context.get("actor_id"),
                    "stage": context.get("stage"),
                    "context": context,
                    "result": json.loads(row["result"]) if row["result"] else None,
                    "error_code": row["error_code"],
                }
            )
        return {
            "operations": result,
            "next_cursor": result[-1]["record_id"] if result else None,
            "has_more": len(rows) > limit,
            "research_only": True,
        }

    def command(self, world_id: str, command_id: str, body: dict) -> dict:
        row = self.db.execute(
            "SELECT body,status,error_code FROM commands WHERE world_id=? AND id=?",
            (world_id, command_id),
        ).fetchone()
        if row:
            if json.loads(row["body"]) != body:
                raise ValueError("COMMAND_ID_REUSED")
            return {
                "command_id": command_id,
                "status": row["status"],
                "error_code": row["error_code"],
                "existing": True,
            }
        self.db.execute(
            "INSERT INTO commands VALUES(?,?,?,'accepted',NULL)",
            (world_id, command_id, canonical(body)),
        )
        return {"command_id": command_id, "status": "accepted", "existing": False}

    def finish_command(
        self, world_id: str, command_id: str, status: str, code: str | None = None
    ) -> None:
        self.db.execute(
            "UPDATE commands SET status=?,error_code=? WHERE world_id=? AND id=?",
            (status, code, world_id, command_id),
        )

    def call_count(self, world_id: str) -> int:
        return self.db.execute(
            "SELECT COUNT(*) FROM operations WHERE world_id=?", (world_id,)
        ).fetchone()[0]

    def get_command(self, world_id: str, command_id: str) -> dict:
        row = self.db.execute(
            "SELECT status,error_code FROM commands WHERE world_id=? AND id=?",
            (world_id, command_id),
        ).fetchone()
        if not row:
            raise KeyError(command_id)
        return {"command_id": command_id, **dict(row)}
