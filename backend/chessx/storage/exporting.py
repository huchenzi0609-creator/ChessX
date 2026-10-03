"""Build consistent ZIP archives with one state/event in memory at a time."""

import copy
import json
import sqlite3
import zipfile
from pathlib import Path

from chessx.api.projections import observer, public_event
from chessx.storage.store import Store, canonical, digest


def build_archive(
    snapshot_path: Path, output_path: Path, world_id: str, view: str, *, secret: str = ""
) -> None:
    db = sqlite3.connect(f"file:{snapshot_path}?mode=ro", uri=True)
    db.row_factory = sqlite3.Row

    def encoded(value):
        text = canonical(value)
        if secret and (secret in text or json.dumps(secret)[1:-1] in text):
            raise ValueError("SECRET_EXPORT_BLOCKED")
        return text.encode("utf-8")

    def stream_array(archive, filename, values):
        with archive.open(filename, "w", force_zip64=True) as output:
            output.write(b"[")
            first = True
            for value in values:
                if not first:
                    output.write(b",")
                output.write(encoded(value))
                first = False
            output.write(b"]")

    try:
        row = db.execute("SELECT state FROM worlds WHERE id=?", (world_id,)).fetchone()
        if row is None:
            raise KeyError(world_id)
        state = json.loads(row["state"])
        with zipfile.ZipFile(output_path, "w", zipfile.ZIP_DEFLATED, allowZip64=True) as archive:
            archive.writestr(
                "manifest.json",
                encoded(
                    {
                        "schema_version": state.get("save_schema_version", 1),
                        "rule_profile": state["rule_profile"],
                        "world_id": world_id,
                        "mode": state["mode"],
                        "view": view,
                        "seq": state["seq"],
                        "scenario_hash": state["scenario_hash"],
                        "rule_manifest_hash": state.get("rule_manifest_hash"),
                        "model_config_hash": state.get("model_config_hash"),
                        "prompt_hash": state.get("prompt_hash"),
                        "actor_order": state["actor_order"],
                        "execution_policy": state.get("execution_policy"),
                        "execution_policy_hash": state.get("execution_policy_hash"),
                        "checkpoint_format": "full-export-from-lossless-journal-v2",
                    }
                ),
            )
            archive.writestr(
                "world.json", encoded(state if view == "research" else observer(state))
            )
            scenario = copy.deepcopy(state["scenario"])
            if view != "research":
                for piece in scenario["pieces"]:
                    piece.pop("private_text", None)
            archive.writestr("scenario.json", encoded(scenario))
            archive.writestr("stories.json", encoded(state["stories"]))
            with archive.open("events.jsonl", "w", force_zip64=True) as output:
                for row in db.execute(
                    "SELECT event FROM events WHERE world_id=? AND seq<=? ORDER BY seq",
                    (world_id, state["seq"]),
                ):
                    event = json.loads(row["event"])
                    event = event if view == "research" else public_event(event)
                    if event is not None:
                        output.write(encoded(event) + b"\n")

            def checkpoints():
                row = db.execute(
                    "SELECT state,checksum FROM initial_states WHERE world_id=?", (world_id,)
                ).fetchone()
                if row is None:
                    raise ValueError("INITIAL_CHECKPOINT_MISSING")
                current = json.loads(row["state"])
                if digest(current) != row["checksum"]:
                    raise ValueError("CHECKPOINT_CORRUPTED")
                yield {"seq": 0, "state": current if view == "research" else observer(current)}
                for row in db.execute(
                    "SELECT seq,event,state,storage_kind,base_seq FROM events WHERE world_id=? AND seq<=? ORDER BY seq",
                    (world_id, state["seq"]),
                ):
                    current = Store._restore_row(current, row)
                    yield {
                        "seq": row["seq"],
                        "state": current if view == "research" else observer(current),
                    }

            stream_array(archive, "checkpoints.json", checkpoints())
            if view == "research":
                stream_array(
                    archive,
                    "private/operations.json",
                    (
                        dict(row)
                        for row in db.execute(
                            "SELECT slot,attempt,status,context,result,error_code FROM operations WHERE world_id=? ORDER BY rowid",
                            (world_id,),
                        )
                    ),
                )
    finally:
        db.close()
