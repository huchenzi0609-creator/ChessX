"""FastAPI wiring and local-only lifecycle; no API keys reach browser responses."""

import asyncio
import copy
import io
import zipfile
from contextlib import asynccontextmanager
from typing import Literal
from uuid import uuid4

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response, StreamingResponse
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from chessx.api.projections import observer, perspective, public_event
from chessx.config import ROOT, Settings
from chessx.domain.errors import RuleError
from chessx.domain.models import Scenario
from chessx.domain.scenarios import create_world, load_scenario
from chessx.llm import ModelError
from chessx.llm.provider import SYSTEM_PROTOCOL
from chessx.runtime.runner import WorldRunner
from chessx.storage import Store
from chessx.storage.store import canonical, digest


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


def create_app(settings: Settings | None = None, *, store: Store | None = None) -> FastAPI:
    settings = settings or Settings.load()

    @asynccontextmanager
    async def lifespan(app):
        app.state.store = store or Store(settings.data_dir / "chessx.sqlite3")
        app.state.runner = WorldRunner(app.state.store, settings)
        app.state.runner.recover()
        yield
        await app.state.runner.close()
        if store is None:
            app.state.store.close()

    app = FastAPI(title="ChessX", version="0.1.0", lifespan=lifespan)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[
            "http://localhost:5173",
            "http://127.0.0.1:5173",
            "http://localhost:8000",
            "http://127.0.0.1:8000",
        ],
        allow_methods=["GET", "POST"],
        allow_headers=["Content-Type", "Last-Event-ID"],
    )

    @app.middleware("http")
    async def local_origin(request, call_next):
        origin = request.headers.get("origin")
        allowed = {
            "http://localhost:5173",
            "http://127.0.0.1:5173",
            "http://localhost:8000",
            "http://127.0.0.1:8000",
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

    @app.get("/api/health")
    async def health():
        return {
            "status": "ok",
            "mode": "local",
            "model_configured": settings.configured,
            "tokenizer_agreement": settings.tokenizer_agreement,
            "rule_version": "2.1",
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
        scenario, personas = load_scenario(ROOT / "scenarios" / (sid + ".json"))
        value = scenario.model_dump(exclude_none=True)
        for piece in value["pieces"]:
            piece.pop("private_file", None)
            piece["private_text"] = personas[piece["id"]]
        return value

    @app.post("/api/scenarios/validate")
    async def validate_scenario(value: dict):
        reject_embedded_key(value)
        try:
            parsed = Scenario.model_validate(value)
            if any(p.private_file for p in parsed.pieces):
                return {
                    "valid": False,
                    "errors": [
                        {
                            "path": "pieces.private_file",
                            "message": "API imports must use inline private_text, not server file paths.",
                        }
                    ],
                }
            return {"valid": True, "errors": []}
        except ValidationError as error:
            return {
                "valid": False,
                "errors": [
                    {"path": ".".join(map(str, item["loc"])), "message": item["type"]}
                    for item in error.errors()
                ],
            }

    @app.post("/api/worlds", status_code=201)
    async def world(body: CreateWorld):
        reject_embedded_key(body.scenario.model_dump())
        if any(p.private_file for p in body.scenario.pieces):
            raise HTTPException(422, "INLINE_PERSONA_REQUIRED")
        state = create_world(
            body.scenario,
            {p.id: p.private_text or "" for p in body.scenario.pieces},
            "world_" + uuid4().hex,
        )
        state.update(
            mode=body.mode,
            error=None,
            scenario_hash=digest(
                {"scenario": body.scenario.model_dump(), "rule_manifest": state["rule_manifest"]}
            ),
            scenario=body.scenario.model_dump(),
            model_config_hash=digest(
                {
                    "model": settings.model,
                    "provider_profile": settings.provider_profile,
                    "response_format": settings.response_format,
                    "tokenizer": settings.tokenizer_name,
                    "context_capacity": settings.context_capacity,
                }
            ),
            prompt_hash=digest(SYSTEM_PROTOCOL),
        )
        if body.mode == "live":
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
                        "rule_version": "2.1",
                    },
                    "audience": [],
                }
            ],
            expected_seq=0,
            expected_generation=0,
        )
        return observer(state)

    @app.get("/api/worlds")
    async def worlds():
        return [
            {
                k: state[k]
                for k in ("world_id", "lifecycle", "turn", "completed_turn", "title", "mode")
            }
            for state in app.state.store.list_worlds()
        ]

    @app.get("/api/worlds/{wid}")
    async def state(wid: str):
        value = observer(get_state(wid))
        value["usage"]["calls"] = app.state.store.call_count(wid)
        return value

    @app.get("/api/worlds/{wid}/perspectives/{pid}")
    async def role(wid: str, pid: str):
        state = get_state(wid)
        if pid not in state["pieces"]:
            raise HTTPException(404, "PIECE_NOT_FOUND")
        return perspective(state, pid)

    @app.get("/api/worlds/{wid}/inspection/{pid}")
    async def inspect(wid: str, pid: str):
        state = get_state(wid)
        if pid not in state["pieces"]:
            raise HTTPException(404, "PIECE_NOT_FOUND")
        return {
            "piece_id": pid,
            "private": copy.deepcopy(state["private"][pid]),
            "operations": app.state.store.operation_records(wid, pid),
            "research_only": True,
        }

    @app.post("/api/worlds/{wid}/commands", status_code=202)
    async def command(wid: str, body: Command):
        state = get_state(wid)
        if body.kind == "reset":
            record = app.state.store.command(wid, body.command_id, body.model_dump())
            if record.pop("existing"):
                return record
            app.state.runner.pause(wid)
            new = copy.deepcopy(state["scenario"])
            created = await world(
                CreateWorld(scenario=Scenario.model_validate(new), mode=state["mode"])
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

    @app.get("/api/worlds/{wid}/stream")
    async def stream(wid: str, request: Request, after_seq: int = 0):
        get_state(wid)
        header = request.headers.get("last-event-id", "")
        if header.startswith(wid + ":"):
            try:
                after_seq = max(after_seq, int(header.split(":")[-1]))
            except ValueError:
                pass

        async def generate():
            cursor = after_seq
            while not await request.is_disconnected():
                page = app.state.store.events(wid, cursor, 200)
                for event in page["events"]:
                    if (view := public_event(event)) is not None:
                        yield f"id: {event['event_id']}\nevent: world\ndata: {canonical(view)}\n\n"
                cursor = page["next_cursor"]
                yield f"event: cursor\ndata: {cursor}\n\n"
                await asyncio.sleep(0.25)

        return StreamingResponse(generate(), media_type="text/event-stream")

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
        state["phase"] = "BARD"
        state["cursor"] = {"story_done": False}
        state["lifecycle"] = "PAUSED"
        segment = state["segments"][-1]
        segment["status"] = "running"
        segment["story_revision"] = segment.get("story_revision", 0) + 1
        segment.pop("story_job", None)
        state["target_turn"] = state["completed_turn"]
        app.state.store.commit(
            state,
            [
                {
                    "type": "StoryRegenerationRequested",
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

    @app.post("/api/worlds/{wid}/export")
    async def export(wid: str, body: Export):
        state = get_state(wid)
        archive = io.BytesIO()
        all_events = []
        cursor = 0
        while True:
            page = app.state.store.events(wid, cursor, 1000)
            all_events.extend(page["events"])
            if not page["has_more"]:
                break
            cursor = page["next_cursor"]
        with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as z:
            z.writestr(
                "manifest.json",
                canonical(
                    {
                        "schema_version": "2.1",
                        "rule_profile": "demo-v2.1",
                        "world_id": wid,
                        "mode": state["mode"],
                        "view": body.view,
                        "seq": state["seq"],
                        "scenario_hash": state["scenario_hash"],
                        "rule_manifest_hash": state.get("rule_manifest_hash"),
                        "model_config_hash": state.get("model_config_hash"),
                        "prompt_hash": state.get("prompt_hash"),
                        "actor_order": state["actor_order"],
                    }
                ),
            )
            z.writestr(
                "world.json", canonical(state if body.view == "research" else observer(state))
            )
            scenario = copy.deepcopy(state["scenario"])
            if body.view != "research":
                for piece in scenario["pieces"]:
                    piece.pop("private_text", None)
            z.writestr("scenario.json", canonical(scenario))
            views = (
                all_events
                if body.view == "research"
                else [v for e in all_events if (v := public_event(e)) is not None]
            )
            z.writestr("events.jsonl", "\n".join(canonical(e) for e in views) + "\n")
            z.writestr("stories.json", canonical(state["stories"]))
            snapshots = app.state.store.db.execute(
                "SELECT seq,state FROM events WHERE world_id=? ORDER BY seq", (wid,)
            ).fetchall()
            checkpoints = [{"seq": 0, "state": app.state.store.replay(wid, 0)}]
            checkpoints.extend(
                {"seq": row["seq"], "state": __import__("json").loads(row["state"])}
                for row in snapshots
            )
            if body.view != "research":
                checkpoints = [
                    {"seq": point["seq"], "state": observer(point["state"])}
                    for point in checkpoints
                ]
            z.writestr("checkpoints.json", canonical(checkpoints))
            if body.view == "research":
                rows = app.state.store.db.execute(
                    "SELECT slot,attempt,status,context,result,error_code FROM operations WHERE world_id=?",
                    (wid,),
                ).fetchall()
                z.writestr("private/operations.json", canonical([dict(r) for r in rows]))
        return Response(
            archive.getvalue(),
            media_type="application/zip",
            headers={"Content-Disposition": f'attachment; filename="{wid}-{body.view}.zip"'},
        )

    return app
