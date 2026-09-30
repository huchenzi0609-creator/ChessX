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
from pathlib import Path
from typing import Any


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
        INSERT OR IGNORE INTO schema_version VALUES(1);
        CREATE TABLE IF NOT EXISTS worlds(id TEXT PRIMARY KEY, seq INTEGER NOT NULL, generation INTEGER NOT NULL, state TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS initial_states(world_id TEXT PRIMARY KEY REFERENCES worlds(id), state TEXT NOT NULL, checksum TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS events(world_id TEXT NOT NULL REFERENCES worlds(id), seq INTEGER NOT NULL, event TEXT NOT NULL, state TEXT NOT NULL, PRIMARY KEY(world_id,seq));
        CREATE TABLE IF NOT EXISTS operations(world_id TEXT NOT NULL, slot TEXT NOT NULL, attempt INTEGER NOT NULL, generation INTEGER NOT NULL, context_hash TEXT NOT NULL, context TEXT NOT NULL, status TEXT NOT NULL, result TEXT, error_code TEXT, PRIMARY KEY(world_id,slot,attempt));
        CREATE TABLE IF NOT EXISTS commands(world_id TEXT NOT NULL, id TEXT NOT NULL, body TEXT NOT NULL, status TEXT NOT NULL, error_code TEXT, PRIMARY KEY(world_id,id));
        CREATE TABLE IF NOT EXISTS text_versions(world_id TEXT NOT NULL, owner TEXT NOT NULL, kind TEXT NOT NULL, version INTEGER NOT NULL, payload TEXT NOT NULL, PRIMARY KEY(world_id,owner,kind,version));
        """)

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

        state = copy.deepcopy(state)
        old = self.get(state["world_id"])
        if old["seq"] != expected_seq or old["generation"] != expected_generation:
            raise ConflictError()
        drafts = drafts or [{"type": "CursorAdvanced", "payload": {}, "audience": []}]
        assert_invariants(state)
        prior_hash = digest(old)
        records = []
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
            event["resulting_state_hash"] = digest(state)
            prior_hash = event["resulting_state_hash"]
            records.append((seq, canonical(event), canonical(state)))
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
                "INSERT INTO events VALUES(?,?,?,?)", [(state["world_id"], *r) for r in records]
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
            "SELECT state,event FROM events WHERE world_id=? AND seq<=? ORDER BY seq DESC LIMIT 1",
            (world_id, seq),
        ).fetchone()
        if row is None:
            raise KeyError(seq)
        state = json.loads(row["state"])
        if digest(state) != json.loads(row["event"])["resulting_state_hash"]:
            raise ValueError("CHECKPOINT_CORRUPTED")
        return state

    def prepare(self, world_id: str, slot: str, generation: int, context: dict) -> int:
        attempt = self.db.execute(
            "SELECT COALESCE(MAX(attempt),0)+1 FROM operations WHERE world_id=? AND slot=?",
            (world_id, slot),
        ).fetchone()[0]
        self.db.execute(
            "INSERT INTO operations VALUES(?,?,?,?,?,?,'pending',NULL,NULL)",
            (world_id, slot, attempt, generation, digest(context), canonical(context)),
        )
        return attempt

    def fail_operation(
        self, world_id: str, slot: str, attempt: int, code: str, obsolete: bool = False
    ) -> None:
        self.db.execute(
            "UPDATE operations SET status=?,error_code=? WHERE world_id=? AND slot=? AND attempt=?",
            ("obsolete" if obsolete else "failed", code, world_id, slot, attempt),
        )

    def operation_records(self, world_id: str, owner: str) -> list[dict]:
        rows = self.db.execute(
            "SELECT slot,attempt,status,context,result,error_code FROM operations WHERE world_id=? ORDER BY rowid DESC LIMIT 100",
            (world_id,),
        ).fetchall()
        result = []
        for row in rows:
            context = json.loads(row["context"])
            if context.get("actor_id") == owner:
                result.append(
                    {
                        "slot_id": row["slot"],
                        "attempt": row["attempt"],
                        "status": row["status"],
                        "context": context,
                        "result": json.loads(row["result"]) if row["result"] else None,
                        "error_code": row["error_code"],
                    }
                )
        return result

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
