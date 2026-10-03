"""FastAPI wiring and local-only lifecycle; no API keys reach browser responses."""

import asyncio
import copy
import os
import tempfile
import threading
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Literal
from uuid import uuid4

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from starlette.background import BackgroundTask

from chessx.api.presets import register_presets
from chessx.api.projections import (
    conversation_messages,
    observer,
    perspective,
    public_event,
    receipt_page,
)
from chessx.config import ROOT, Settings
from chessx.domain.errors import RuleError
from chessx.domain.models import Scenario
from chessx.domain.scenarios import create_world, load_scenario
from chessx.llm import ModelError
from chessx.llm.provider import prompt_manifest
from chessx.presets import PresetError, PresetLibrary
from chessx.runtime.runner import WorldRunner
from chessx.runtime.versioning import (
    SAVE_SCHEMA_VERSION,
    execution_policy_hash,
    frozen_execution_policy,
    policy_id,
    upgrade_preview,
)
from chessx.storage import Store
from chessx.storage.exporting import build_archive
from chessx.storage.store import ConflictError, canonical, digest


class CreateWorld(BaseModel):
    model_config = ConfigDict(extra="forbid")
    scenario: Scenario
    mode: Literal["fixture", "live"] = "live"


class Budget(BaseModel):
    model_config = ConfigDict(extra="forbid")
    max_calls: int = Field(default=1000, ge=1, le=10000)
    max_total_tokens: int = Field(default=2_000_000, ge=4096, le=100_000_000)
    max_wall_seconds: int = Field(default=1800, ge=1, le=86400)


class Command(BaseModel):
    model_config = ConfigDict(extra="forbid")
    command_id: str = Field(min_length=1, max_length=128)
    kind: Literal["run", "pause", "resume", "step_event", "step_turn", "retry", "reset"]
    turns: int = Field(default=1, ge=1, le=500)
    budget: Budget = Field(default_factory=Budget)


class Export(BaseModel):
    model_config = ConfigDict(extra="forbid")
    view: Literal["observer", "research"] = "observer"


class UpgradePolicy(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_seq: int = Field(ge=0)


def create_app(settings: Settings | None = None, *, store: Store | None = None) -> FastAPI:
    settings = settings or Settings.load()

    @asynccontextmanager
    async def lifespan(app):
        app.state.store = store or Store(settings.data_dir / "chessx.sqlite3")
        # Export artifacts are temporary; backups live in a separate directory.
        exports_dir = settings.data_dir / "exports"
        if exports_dir.is_dir():
            for pattern in ("archive_*.zip", "backup_*.sqlite3"):
                for path in exports_dir.glob(pattern):
                    path.unlink(missing_ok=True)
        app.state.runner = WorldRunner(app.state.store, settings)
        app.state.presets = PresetLibrary(ROOT / "presets", settings.data_dir / "presets")
        app.state.runner.recover()
        yield
        await app.state.runner.close()
        for path, _filename, _expiry in downloads.values():
            path.unlink(missing_ok=True)
        downloads.clear()
        if store is None:
            app.state.store.close()

    app = FastAPI(title="ChessX", version="0.2.0", lifespan=lifespan)
    downloads: dict[str, tuple[Path, str, float]] = {}
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[
            "http://localhost:5180",
            "http://127.0.0.1:5180",
            "http://localhost:8001",
            "http://127.0.0.1:8001",
        ],
        allow_methods=["GET", "POST", "PUT", "DELETE"],
        allow_headers=["Content-Type", "Last-Event-ID"],
    )

    @app.middleware("http")
    async def local_origin(request, call_next):
        origin = request.headers.get("origin")
        allowed = {
            "http://localhost:5180",
            "http://127.0.0.1:5180",
            "http://localhost:8001",
            "http://127.0.0.1:8001",
        }
        if origin and origin not in allowed:
            return JSONResponse(
                {"code": "ORIGIN_REJECTED", "message": "Only the local UI is supported."},
                status_code=403,
            )
        host = request.headers.get("host", "").split(":")[0]
        if host not in {"localhost", "127.0.0.1", "testserver"}:
            return JSONResponse(
                {"code": "HOST_REJECTED", "message": "This server is local-only."}, status_code=403
            )
        response = await call_next(request)
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        return response

    @app.exception_handler(RequestValidationError)
    async def bad_input(request, error):
        return JSONResponse(
            {
                "code": "REQUEST_INVALID",
                "message": "Request fields failed validation.",
                "fields": [
                    {
                        "path": ".".join(str(x) for x in item["loc"][:-1]) + ".[unknown_field]"
                        if item["type"] == "extra_forbidden"
                        else ".".join(str(x) for x in item["loc"]),
                        "type": item["type"],
                    }
                    for item in error.errors()
                ],
            },
            status_code=422,
        )

    @app.exception_handler(RuleError)
    async def rule_error(request, error):
        return JSONResponse({"code": error.code, "message": error.message}, status_code=409)

    @app.exception_handler(ModelError)
    async def model_error(request, error):
        return JSONResponse(
            {
                "code": error.code,
                "message": "Model capability/configuration verification did not succeed. No raw upstream response is disclosed.",
            },
            status_code=409,
        )

    @app.exception_handler(ValueError)
    async def invalid_command(request, error):
        return JSONResponse(
            {"code": "COMMAND_INVALID", "message": "The command identifier or fields are invalid."},
            status_code=409,
        )

    def get_state(wid):
        try:
            return app.state.store.get(wid)
        except KeyError:
            raise HTTPException(404, "WORLD_NOT_FOUND") from None

    def reject_embedded_key(value):
        # Compare only inside the server. Never include the candidate in errors.
        key = settings.api_key.get_secret_value()
        if key and key in canonical(value):
            raise HTTPException(422, "SECRET_IN_SCENARIO")

    def task_running(wid):
        task = app.state.runner.tasks.get(wid)
        return task is not None and not task.done()

    def maintenance_busy():
        return (
            any(not task.done() for task in app.state.runner.tasks.values())
            or app.state.store.db.execute(
                "SELECT COUNT(*) FROM operations WHERE status='pending'"
            ).fetchone()[0]
            > 0
        )

    async def background_backup(directory):
        cancelled = threading.Event()

        def work():
            metadata = app.state.store.backup(directory)
            if cancelled.is_set():
                (directory / metadata["filename"]).unlink(missing_ok=True)
            return metadata

        task = asyncio.create_task(asyncio.to_thread(work))
        try:
            return await asyncio.shield(task)
        except asyncio.CancelledError:
            cancelled.set()

            def cleanup(completed):
                if not completed.cancelled() and completed.exception() is None:
                    (directory / completed.result()["filename"]).unlink(missing_ok=True)

            task.add_done_callback(cleanup)
            raise

    def execution_config_snapshot():
        # Explicit allowlist; neither secret nor raw provider URL is serialized.
        value = settings.model_dump(
            include={
                "model",
                "provider_profile",
                "response_format",
                "tokenizer_name",
                "tokenizer_agreement",
                "timeout_seconds",
                "context_capacity",
                "character_input_limit",
                "dialogue_parallelism",
                "memory_batch_size",
                "memory_compaction_mode",
            }
        )
        reject_embedded_key(value)
        return value

    def snapshot(value, *, current=True):
        result = observer(value, compact=True)
        store = app.state.store
        calls = store.call_count(value["world_id"]) if current else value["usage"].get("calls", 0)
        result["usage"]["calls"] = calls
        pending = store.pending_metadata(value["world_id"]) if current else []
        used_tokens = sum(
            value["usage"].get(key, 0)
            for key in ("input_tokens", "output_tokens", "reasoning_tokens")
        )
        limits = value.get("budget", {})
        used_calls = max(0, calls - value.get("budget_base_calls", 0))
        used_tokens = max(0, used_tokens - value.get("budget_base_tokens", 0))
        active = task_running(value["world_id"]) if current else False
        elapsed = (
            max(0, time.monotonic() - getattr(app.state.runner, "_started_at", time.monotonic()))
            if active
            else None
        )
        waiting = (
            "model_request"
            if pending
            else "executing"
            if active
            else value.get("error", {}).get("code")
            if value.get("error")
            else value.get("lifecycle", "idle").lower()
        )
        cursor = value.get("cursor", {})
        result["runtime"] = {
            "budget": {
                "limits": copy.deepcopy(limits),
                "used": {"calls": used_calls, "tokens": used_tokens, "wall_seconds": elapsed},
                "remaining": {
                    "calls": max(0, limits["max_calls"] - used_calls)
                    if "max_calls" in limits
                    else None,
                    "tokens": max(0, limits["max_total_tokens"] - used_tokens)
                    if "max_total_tokens" in limits
                    else None,
                    "wall_seconds": max(0, limits["max_wall_seconds"] - elapsed)
                    if elapsed is not None and "max_wall_seconds" in limits
                    else None,
                },
                "cost": {"known": False, "amount": None, "currency": None},
                "usage_completeness": "known-recorded-tokens-only",
                "unknown_usage_requests": store.unknown_usage_count(value["world_id"])
                if current
                else None,
            },
            "pending_requests": pending,
            "waiting_reason": waiting,
            "active_actor_id": pending[0].get("actor_id")
            if pending
            else cursor.get("work", {}).get("actor_id")
            if cursor.get("work")
            else None,
            "active_stage": pending[0].get("stage")
            if pending
            else cursor.get("work", {}).get("stage")
            if cursor.get("work")
            else value.get("phase"),
            "execution_policy": policy_id(value),
            "save_schema_version": value.get("save_schema_version", 1),
        }
        return result

    @app.get("/api/health")
    async def health():
        return {
            "status": "ok",
            "mode": "local",
            "model_configured": settings.configured,
            "tokenizer_agreement": settings.tokenizer_agreement,
            "rule_version": "2.2",
            "save_schema_version": SAVE_SCHEMA_VERSION,
            "execution_policy": frozen_execution_policy()["id"],
        }

    @app.get("/api/scenarios")
    async def scenarios():
        result = []
        for path in sorted((ROOT / "scenarios").glob("*.json")):
            scenario, _ = load_scenario(path)
            result.append(
                {
                    "id": path.stem,
                    "title": scenario.title,
                    "description": f"{len(scenario.pieces)}个角色 · {scenario.board.rows}×{scenario.board.cols}",
                }
            )
        return result

    @app.get("/api/scenarios/{sid}")
    async def scenario(sid: str):
        if sid not in {p.stem for p in (ROOT / "scenarios").glob("*.json")}:
            raise HTTPException(404, "SCENARIO_NOT_FOUND")
        config, _ = load_scenario(ROOT / "scenarios" / (sid + ".json"))
        return config.model_dump(exclude_none=True)

    def resolve_designs(config: Scenario) -> dict[str, str]:
        designs = {}
        for piece in config.pieces:
            if piece.private_file:
                from pathlib import PurePosixPath

                reference = PurePosixPath(piece.private_file)
                parts = reference.parts
                if (
                    reference.is_absolute()
                    or len(parts) != 4
                    or parts[1] != "characters"
                    or parts[3] != "design.md"
                ):
                    raise HTTPException(422, "PERSONA_REFERENCE_INVALID")
                source = app.state.presets.get_character(parts[0], parts[2])
                designs[piece.id] = source["design_markdown"]
            else:
                # Compatibility-only transport. The preset writer forbids inline private JSON.
                designs[piece.id] = piece.private_text or ""
        reject_embedded_key(designs)
        return designs

    @app.post("/api/scenarios/validate")
    async def validate_scenario(value: dict):
        reject_embedded_key(value)
        try:
            parsed = Scenario.model_validate(value)
            resolve_designs(parsed)
            return {"valid": True, "errors": []}
        except (PresetError, HTTPException):
            return {
                "valid": False,
                "errors": [
                    {
                        "path": "pieces.private_file",
                        "message": "Character Markdown reference cannot be resolved.",
                    }
                ],
            }
        except ValidationError as error:
            return {
                "valid": False,
                "errors": [
                    {"path": ".".join(map(str, item["loc"])), "message": item["type"]}
                    for item in error.errors()
                ],
            }

    async def materialize_world(config, personas, mode, *, preset_source=None, frozen_source=None):
        reject_embedded_key({"scenario": config.model_dump(), "designs": personas})
        state = create_world(
            config,
            personas,
            "world_" + uuid4().hex,
        )
        state.update(
            mode=mode,
            error=None,
            scenario_hash=digest(
                {"scenario": config.model_dump(), "rule_manifest": state["rule_manifest"]}
            ),
            scenario=config.model_dump(exclude_none=True),
            model_config_hash=digest(execution_config_snapshot()),
            provider_endpoint_hash=digest(settings.base_url),
            prompt_hash=digest(prompt_manifest()),
            execution_config=execution_config_snapshot(),
        )
        if frozen_source is not None:
            if frozen_source.get("discipline") is not None:
                state["discipline"] = frozen_source["discipline"]
                state["discipline_hash"] = frozen_source["discipline_hash"]
        if preset_source:
            state["preset_source"] = preset_source
        state["scenario_hash"] = digest(
            {
                "scenario": config.model_dump(),
                "designs": personas,
                "discipline_hash": state["discipline_hash"],
                "rule_manifest": state["rule_manifest"],
            }
        )
        if mode == "live":
            # Construction verifies configuration without spending an API call.
            # The first explicit run probes within that segment's call budget.
            app.state.runner.adapter(state)
        app.state.store.create(state)
        state = app.state.store.commit(
            state,
            [
                {
                    "type": "WorldCreated",
                    "payload": {
                        "title": state["title"],
                        "mode": state["mode"],
                        "rule_version": state["rule_version"],
                    },
                    "audience": [],
                }
            ],
            expected_seq=0,
            expected_generation=0,
        )
        return snapshot(state)

    @app.post("/api/worlds", status_code=201)
    async def world(body: CreateWorld):
        return await materialize_world(body.scenario, resolve_designs(body.scenario), body.mode)

    register_presets(app, materialize_world, reject_embedded_key)

    @app.get("/api/worlds")
    async def worlds():
        return app.state.store.world_summaries()

    @app.get("/api/worlds/{wid}")
    async def state(wid: str):
        return snapshot(get_state(wid))

    @app.delete("/api/worlds/{wid}")
    async def delete_world(wid: str):
        get_state(wid)
        if task_running(wid):
            raise HTTPException(409, "WORLD_BUSY")
        try:
            app.state.store.delete_world(wid)
        except ConflictError:
            raise HTTPException(409, "WORLD_BUSY") from None
        app.state.runner.adapters.pop(wid, None)
        app.state.runner.tasks.pop(wid, None)
        for token, (path, filename, _expiry) in list(downloads.items()):
            if filename.startswith(wid + "-"):
                path.unlink(missing_ok=True)
                del downloads[token]
        return {"deleted": True, "world_id": wid}

    @app.post("/api/backups", status_code=201)
    async def backup():
        return await background_backup(settings.data_dir / "backups")

    def preview_policy(wid):
        value = get_state(wid)
        return upgrade_preview(
            value,
            task_running=maintenance_busy(),
            pending_operations=app.state.store.pending_count(wid),
            prompt_changed=value.get("prompt_hash") != digest(prompt_manifest()),
            config_changed=value.get("execution_config") != execution_config_snapshot()
            or value.get("provider_endpoint_hash") != digest(settings.base_url),
        )

    @app.get("/api/worlds/{wid}/upgrade-policy")
    async def policy_preview(wid: str):
        return preview_policy(wid)

    @app.post("/api/worlds/{wid}/upgrade-policy")
    async def upgrade_policy(wid: str, body: UpgradePolicy):
        value = get_state(wid)
        preview = preview_policy(wid)
        if body.expected_seq != value["seq"]:
            raise HTTPException(409, "WORLD_CHANGED")
        if not preview["can_upgrade"]:
            return JSONResponse(
                {"code": "POLICY_UPGRADE_BLOCKED", "preview": preview}, status_code=409
            )
        backup = app.state.store.backup(settings.data_dir / "backups")
        from chessx.domain.profile import (
            LATEST_RULE_PROFILE,
            frozen_rule_manifest,
            profile_manifest_hash,
        )

        if not preview["policy_current"]:
            peaks = {}
            for historical in app.state.store.iter_states(wid):
                for cid, conv in historical.get("conversations", {}).items():
                    peaks[cid] = max(
                        peaks.get(cid, 0), len(conv.get("members", [])), conv.get("peak_members", 0)
                    )
            for cid, conv in value.get("conversations", {}).items():
                conv["peak_members"] = max(
                    peaks.get(cid, 0),
                    len(conv.get("members", [])),
                    conv.get("peak_members", 0),
                    *(peaks.get(parent, 0) for parent in conv.get("parent_ids", [])),
                )
                conv["max_messages"] = 50 * conv["peak_members"]
                conv.setdefault(
                    "counted_message_ids",
                    [
                        message.get("message_id") or message["id"]
                        for message in conv.get("messages", [])
                    ],
                )
            speeches, counted = {}, set()
            for conv in value.get("conversations", {}).values():
                if conv.get("kind", "ordinary") != "ordinary":
                    continue
                for message in conv.get("messages", []):
                    mid = message.get("message_id") or message.get("id")
                    if message.get("turn") == value["turn"] and mid not in counted:
                        counted.add(mid)
                        pid = message.get("speaker_id")
                        speeches[pid] = speeches.get(pid, 0) + 1
            value["dialogue_permissions"] = {
                "turn": value["turn"],
                "actors": {
                    pid: {"speeches": count, "permissions": count}
                    for pid, count in speeches.items()
                },
            }
        old_policy = policy_id(value)
        previous_metadata = {
            key: value.get(key)
            for key in (
                "execution_policy_hash",
                "prompt_hash",
                "model_config_hash",
                "provider_endpoint_hash",
            )
        }
        value.update(
            save_schema_version=SAVE_SCHEMA_VERSION,
            execution_policy=frozen_execution_policy(),
            execution_policy_hash=execution_policy_hash(frozen_execution_policy()),
            rule_profile=LATEST_RULE_PROFILE,
            rule_version="2.2",
            rule_manifest=frozen_rule_manifest(),
            rule_manifest_hash=profile_manifest_hash(LATEST_RULE_PROFILE),
            prompt_hash=digest(prompt_manifest()),
            execution_config=execution_config_snapshot(),
            model_config_hash=digest(execution_config_snapshot()),
            provider_endpoint_hash=digest(settings.base_url),
        )
        value.setdefault("execution_history", []).append(
            {
                "from_seq": body.expected_seq,
                "previous": previous_metadata,
                "policy_id": old_policy,
                "backup_id": backup["backup_id"],
            }
        )
        value["generation"] += 1
        value["error"] = None
        if value["lifecycle"] == "PAUSED_ERROR":
            value["lifecycle"] = "PAUSED"
        value = app.state.store.commit(
            value,
            [
                {
                    "type": "ExecutionPolicyUpgraded",
                    "payload": {
                        "from_policy": old_policy,
                        "to_policy": policy_id(value),
                        "backup_id": backup["backup_id"],
                    },
                    "audience": [],
                }
            ],
            expected_seq=body.expected_seq,
            expected_generation=value["generation"] - 1,
        )
        return {"world": snapshot(value), "backup": backup}

    @app.post("/api/worlds/{wid}/compact-journal")
    async def compact_journal(wid: str):
        value = get_state(wid)
        if (
            maintenance_busy()
            or value["lifecycle"] in {"RUNNING", "STEPPING", "PAUSING"}
            or app.state.store.pending_count(wid)
        ):
            raise HTTPException(409, "WORLD_BUSY")
        backup = app.state.store.backup(settings.data_dir / "backups")
        return {**app.state.store.compact_journal(wid), "backup": backup}

    @app.get("/api/worlds/{wid}/perspectives/{pid}")
    async def role(wid: str, pid: str, after_receipt: int | None = None, limit: int = 200):
        state = get_state(wid)
        if pid not in state["pieces"]:
            raise HTTPException(404, "PIECE_NOT_FOUND")
        return perspective(state, pid, after_receipt=after_receipt, limit=limit)

    @app.get("/api/worlds/{wid}/perspectives/{pid}/receipts")
    async def role_receipts(wid: str, pid: str, after_receipt: int = 0, limit: int = 200):
        value = get_state(wid)
        if pid not in value["pieces"]:
            raise HTTPException(404, "PIECE_NOT_FOUND")
        return {
            "world_id": wid,
            "piece_id": pid,
            "seq": value["seq"],
            **receipt_page(value, pid, after_receipt=after_receipt, limit=limit),
        }

    @app.get("/api/worlds/{wid}/inspection/{pid}")
    async def inspect(wid: str, pid: str, before_id: int | None = None, limit: int = 20):
        state = get_state(wid)
        if pid not in state["pieces"]:
            raise HTTPException(404, "PIECE_NOT_FOUND")
        return {
            "piece_id": pid,
            "private": copy.deepcopy(state["private"][pid]),
            **app.state.store.operation_page(
                wid, actor_id=pid, before_id=before_id, limit=min(max(limit, 1), 200)
            ),
            "research_only": True,
        }

    @app.get("/api/worlds/{wid}/inspection")
    async def inspect_operations(
        wid: str,
        actor_id: str | None = None,
        stage: str | None = None,
        before_id: int | None = None,
        limit: int = 50,
    ):
        value = get_state(wid)
        if actor_id is not None and actor_id not in value["pieces"]:
            raise HTTPException(404, "PIECE_NOT_FOUND")
        return app.state.store.operation_page(
            wid, actor_id=actor_id, stage=stage, before_id=before_id, limit=min(max(limit, 1), 200)
        )

    @app.post("/api/worlds/{wid}/commands", status_code=202)
    async def command(wid: str, body: Command):
        state = get_state(wid)
        if body.kind == "reset":
            record = app.state.store.command(wid, body.command_id, body.model_dump())
            if record.pop("existing"):
                return record
            app.state.runner.pause(wid)
            new = copy.deepcopy(state["scenario"])
            designs = {
                pid: private.get("initial_memory", private["persona"])
                for pid, private in state["private"].items()
            }
            created = await materialize_world(
                Scenario.model_validate(new),
                designs,
                state["mode"],
                preset_source=state.get("preset_source"),
                frozen_source=state,
            )
            app.state.store.finish_command(wid, body.command_id, "completed")
            return {
                "command_id": body.command_id,
                "status": "completed",
                "world_id": created["world_id"],
            }
        return await app.state.runner.start(wid, body.model_dump())

    @app.get("/api/worlds/{wid}/commands/{cid}")
    async def command_state(wid: str, cid: str):
        try:
            return app.state.store.get_command(wid, cid)
        except KeyError:
            raise HTTPException(404, "COMMAND_NOT_FOUND") from None

    @app.get("/api/worlds/{wid}/events")
    async def events(wid: str, after_seq: int = 0, limit: int = 200):
        get_state(wid)
        page = app.state.store.events(wid, max(after_seq, 0), min(max(limit, 1), 1000))
        page["events"] = [view for e in page["events"] if (view := public_event(e)) is not None]
        return page

    @app.get("/api/worlds/{wid}/conversations/{cid}/messages")
    async def message_history(
        wid: str,
        cid: str,
        before_index: int | None = None,
        limit: int = 50,
        at_seq: int | None = None,
    ):
        value = get_state(wid)
        if at_seq is not None:
            try:
                value = app.state.store.replay(wid, at_seq)
            except KeyError:
                raise HTTPException(404, "REPLAY_POINT_NOT_FOUND") from None
        if cid not in value.get("conversations", {}):
            raise HTTPException(404, "CONVERSATION_NOT_FOUND")
        return {
            "world_id": wid,
            "conversation_id": cid,
            "seq": value["seq"],
            "turn": value["turn"],
            "lifecycle": value["lifecycle"],
            **conversation_messages(
                value["conversations"][cid], before_index=before_index, limit=limit
            ),
        }

    @app.get("/api/worlds/{wid}/stream")
    async def stream(wid: str, request: Request, after_seq: int = 0):
        initial = get_state(wid)
        header = request.headers.get("last-event-id", "")
        if header.startswith(wid + ":"):
            try:
                after_seq = max(after_seq, int(header.split(":")[-1]))
            except ValueError:
                pass
        after_seq = min(max(after_seq, 0), initial["seq"])

        async def generate():
            cursor = after_seq
            while not await request.is_disconnected():
                try:
                    value = get_state(wid)
                except HTTPException:
                    yield "event: deleted\ndata: {}\n\n"
                    return
                page = app.state.store.events(wid, cursor, 200)
                for event in page["events"]:
                    if (view := public_event(event)) is not None:
                        yield f"id: {event['event_id']}\nevent: world\ndata: {canonical(view)}\n\n"
                cursor = page["next_cursor"]
                # Catch-up pages expose the exact snapshot at their cursor. A
                # live snapshot never races ahead of events already delivered.
                if cursor < value["seq"]:
                    try:
                        value = app.state.store.replay(wid, cursor)
                    except KeyError:
                        yield "event: deleted\ndata: {}\n\n"
                        return
                    view = snapshot(value, current=False)
                else:
                    view = snapshot(value)
                yield f"id: {wid}:{cursor}\nevent: snapshot\ndata: {canonical(view)}\n\n"
                yield f"event: cursor\ndata: {cursor}\n\n"
                yield ": heartbeat\n\n"
                await asyncio.sleep(0 if page["has_more"] else 1)

        return StreamingResponse(
            generate(), media_type="text/event-stream", headers={"X-Accel-Buffering": "no"}
        )

    @app.get("/api/worlds/{wid}/replay")
    async def replay(wid: str, at_seq: int):
        get_state(wid)
        try:
            return observer(app.state.store.replay(wid, at_seq))
        except KeyError:
            raise HTTPException(404, "REPLAY_POINT_NOT_FOUND") from None

    @app.get("/api/worlds/{wid}/segments/{sid}/story")
    async def story(wid: str, sid: str):
        value = get_state(wid)["stories"].get(sid)
        if value is None:
            raise HTTPException(404, "STORY_NOT_FOUND")
        return value

    @app.post("/api/worlds/{wid}/segments/{sid}/story", status_code=202)
    async def regenerate(wid: str, sid: str):
        state = get_state(wid)
        if not state["segments"] or state["segments"][-1]["id"] != sid:
            raise HTTPException(409, "ONLY_LATEST_SEGMENT_SUPPORTED")
        if any(not t.done() for t in app.state.runner.tasks.values()):
            raise HTTPException(409, "WORLD_BUSY")
        segment = state["segments"][-1]
        resuming = state["phase"] == "BARD" and not state.get("cursor", {}).get("story_done")
        if not resuming and sid not in state["stories"]:
            # Never replace a partially executed simulation cursor with a story
            # job just because its unfinished segment is visible in the UI.
            raise HTTPException(409, "STORY_SEGMENT_NOT_READY")
        if not resuming:
            state["phase"] = "BARD"
            state["cursor"] = {"story_done": False}
            segment["story_revision"] = segment.get("story_revision", 0) + 1
            segment.pop("story_job", None)
        state["lifecycle"] = "PAUSED"
        segment["status"] = "running"
        state["target_turn"] = state["completed_turn"]
        app.state.store.commit(
            state,
            [
                {
                    "type": "StoryResumeRequested" if resuming else "StoryRegenerationRequested",
                    "payload": {"segment_id": sid},
                    "audience": [],
                }
            ],
            expected_seq=state["seq"],
            expected_generation=state["generation"],
        )
        return await app.state.runner.start(
            wid,
            {
                "command_id": "story_" + uuid4().hex,
                "kind": "retry",
                "turns": 1,
                "budget": {"max_calls": 10, "max_total_tokens": 100_000, "max_wall_seconds": 180},
            },
        )

    def file_stream(path, filename):
        def chunks():
            with path.open("rb") as source:
                while chunk := source.read(64 * 1024):
                    yield chunk

        return StreamingResponse(
            chunks(),
            media_type="application/zip",
            headers={
                "Content-Disposition": f'attachment; filename="{filename}"',
                "Content-Length": str(path.stat().st_size),
            },
            background=BackgroundTask(path.unlink, missing_ok=True),
        )

    @app.post("/api/worlds/{wid}/export")
    async def export(wid: str, body: Export, download: Literal["prepare"] | None = None):
        get_state(wid)
        directory = settings.data_dir / "exports"
        directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        for token, (old_path, _filename, expiry) in list(downloads.items()):
            if expiry < time.monotonic():
                old_path.unlink(missing_ok=True)
                del downloads[token]
        # A consistent read-only SQLite copy decouples streaming from live
        # runner commits. Never load all events/checkpoints into a Python list.
        fd, temporary = tempfile.mkstemp(prefix="archive_", suffix=".zip", dir=directory)
        os.close(fd)
        path = Path(temporary)
        cancelled = threading.Event()

        def work():
            snapshot_path = None
            try:
                backup = app.state.store.backup(directory)
                snapshot_path = directory / backup["filename"]
                if not cancelled.is_set():
                    build_archive(
                        snapshot_path,
                        path,
                        wid,
                        body.view,
                        secret=settings.api_key.get_secret_value(),
                    )
            except BaseException:
                path.unlink(missing_ok=True)
                raise
            finally:
                if snapshot_path is not None:
                    snapshot_path.unlink(missing_ok=True)
                if cancelled.is_set():
                    path.unlink(missing_ok=True)

        task = asyncio.create_task(asyncio.to_thread(work))
        try:
            await asyncio.shield(task)
            # A concurrent deletion cannot leave a downloadable orphan archive.
            get_state(wid)
        except asyncio.CancelledError:
            cancelled.set()
            path.unlink(missing_ok=True)
            task.add_done_callback(
                lambda completed: None if completed.cancelled() else completed.exception()
            )
            raise
        except KeyError:
            path.unlink(missing_ok=True)
            raise HTTPException(404, "WORLD_NOT_FOUND") from None
        except BaseException:
            path.unlink(missing_ok=True)
            raise
        filename = f"{wid}-{body.view}.zip"
        if download == "prepare":
            token = uuid4().hex
            downloads[token] = (path, filename, time.monotonic() + 3600)
            return {
                "download_url": f"/api/exports/{token}",
                "filename": filename,
                "size_bytes": path.stat().st_size,
            }
        return file_stream(path, filename)

    @app.get("/api/exports/{token}")
    async def download_export(token: str):
        prepared = downloads.pop(token, None)
        if prepared is None:
            raise HTTPException(404, "EXPORT_NOT_FOUND")
        path, filename, expiry = prepared
        if expiry < time.monotonic():
            path.unlink(missing_ok=True)
            raise HTTPException(404, "EXPORT_EXPIRED")
        return file_stream(path, filename)

    return app
