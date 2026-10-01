"""Local authoring endpoints: mechanical JSON and private Markdown remain separate."""

from typing import Literal

from fastapi import FastAPI
from fastapi.responses import JSONResponse
from pydantic import Field

from chessx.domain.models import StrictModel
from chessx.domain.scenarios import frozen_discipline
from chessx.presets import CharacterInformation, PresetError, PresetScenario


class GroupCreate(StrictModel):
    id: str
    title: str
    description: str = ""


class CharacterSave(StrictModel):
    character: CharacterInformation
    design_markdown: str = Field(min_length=1, max_length=262144)
    expected_revision: str | None = None


class ScenarioSave(StrictModel):
    scenario: PresetScenario
    expected_revision: str | None = None


class PresetLaunch(StrictModel):
    preset_id: str
    mode: Literal["fixture", "live"] = "fixture"


def register_presets(app: FastAPI, materialize_world, reject_secret) -> None:
    @app.exception_handler(PresetError)
    async def preset_error(request, error):
        status = (
            404
            if error.code == "NOT_FOUND"
            else 409
            if error.code in {"REVISION_CONFLICT", "ALREADY_EXISTS", "READ_ONLY"}
            else 422
        )
        return JSONResponse({"code": error.code, "message": error.message}, status_code=status)

    @app.get("/api/discipline")
    async def discipline():
        markdown, revision = frozen_discipline()
        reject_secret(markdown)
        return {"markdown": markdown, "revision": revision}

    @app.get("/api/presets")
    async def groups():
        value = app.state.presets.list_groups()
        reject_secret(value)
        return value

    @app.post("/api/presets", status_code=201)
    async def create_group(body: GroupCreate):
        reject_secret(body.model_dump())
        return app.state.presets.create_group(body.id, body.title, body.description)

    @app.get("/api/characters")
    async def characters():
        value = app.state.presets.list_characters()
        reject_secret(value)
        return value

    @app.get("/api/presets/{gid}")
    async def group(gid: str):
        value = app.state.presets.get_group(gid)
        reject_secret(value)
        return value

    @app.get("/api/presets/{gid}/characters/{cid}")
    async def character(gid: str, cid: str):
        value = app.state.presets.get_character(gid, cid)
        reject_secret(value)
        return value

    @app.put("/api/presets/{gid}/characters/{cid}")
    async def save_character(gid: str, cid: str, body: CharacterSave):
        reject_secret(body.model_dump())
        return app.state.presets.save_character(
            gid, cid, body.character.model_dump(), body.design_markdown, body.expected_revision
        )

    @app.get("/api/presets/{gid}/scenario")
    async def scenario(gid: str):
        value = app.state.presets.get_group(gid)
        reject_secret(value)
        return {"scenario": value["scenario"], "revision": value["scenario_revision"]}

    @app.put("/api/presets/{gid}/scenario")
    async def save_scenario(gid: str, body: ScenarioSave):
        reject_secret(body.model_dump())
        return app.state.presets.save_scenario(
            gid, body.scenario.model_dump(), body.expected_revision
        )

    @app.post("/api/presets/{gid}/validate")
    async def validate(gid: str):
        try:
            config, personas = app.state.presets.build_scenario(gid)
            reject_secret({"scenario": config.model_dump(), "designs": personas})
            return {"valid": True, "errors": []}
        except PresetError as error:
            return {
                "valid": False,
                "errors": [{"path": "characters", "message": error.message, "code": error.code}],
            }

    @app.post("/api/worlds/from-preset", status_code=201)
    async def launch(body: PresetLaunch):
        config, personas = app.state.presets.build_scenario(body.preset_id)
        source = app.state.presets.get_group(body.preset_id)
        reject_secret({"scenario": config.model_dump(), "designs": personas})
        return await materialize_world(
            config,
            personas,
            body.mode,
            preset_source={
                "id": body.preset_id,
                "scenario_revision": source["scenario_revision"],
                "characters": source["scenario"]["characters"],
            },
        )
