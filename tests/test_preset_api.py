"""Disk authoring and world initialization via real HTTP contracts; no provider calls."""

import json

import pytest
from chessx.api.app import create_app
from chessx.config import Settings
from fastapi.testclient import TestClient
from pydantic import SecretStr


@pytest.fixture
def client(tmp_path):
    with TestClient(create_app(Settings(data_dir=tmp_path / "data"))) as value:
        yield value, tmp_path / "data" / "presets"


def group(client, gid="custom"):
    result = client.post(
        "/api/presets", json={"id": gid, "title": "我的预设", "description": "作者说明"}
    )
    assert result.status_code == 201, result.text
    return result.json()


def character(cid="hero"):
    return {
        "id": cid,
        "name": "旅者",
        "type": "pawn",
        "color": "青",
        "position": {"row": 2, "col": 2},
    }


def test_structured_character_disk_pair_and_cross_group_world(client):
    client, root = client
    group(client, "cast")
    body = {
        "character": character(),
        "design_markdown": "# 旅者\n## 公开目标\n只在我选择说出后别人才能知道。\n## 隐藏目标\nPRIVATE_STORY_823\n## 过往故事\n我曾救过一位信使。",
    }
    response = client.put("/api/presets/cast/characters/hero", json=body)
    assert response.status_code == 200, response.text
    folder = root / "cast" / "characters" / "hero"
    info = json.loads((folder / "character.json").read_text())
    assert set(info) == {"id", "name", "type", "color", "position"}
    assert (folder / "design.md").read_text() == body["design_markdown"]
    assert "PRIVATE_STORY_823" not in (folder / "character.json").read_text()
    group(client, "game")
    config = client.get("/api/presets/game/scenario").json()
    scenario = config["scenario"]
    scenario["characters"] = [
        {
            "group_id": "cast",
            "character_id": "hero",
            "piece_id": "traveler",
            "position": {"row": 4, "col": 4},
        },
        {"group_id": "the-seal", "character_id": "messenger"},
    ]
    assert (
        client.put(
            "/api/presets/game/scenario",
            json={"scenario": scenario, "expected_revision": config["revision"]},
        ).status_code
        == 200
    )
    assert client.post("/api/presets/game/validate").json()["valid"]
    response = client.post("/api/worlds/from-preset", json={"preset_id": "game", "mode": "fixture"})
    assert response.status_code == 201, response.text
    world = response.json()
    assert set(world["pieces"]) == {"traveler", "messenger"}
    assert world["pieces"]["traveler"]["position"] == {"row": 4, "col": 4}
    assert "PRIVATE_STORY_823" not in response.text
    private = client.get(f"/api/worlds/{world['world_id']}/perspectives/traveler").json()["private"]
    assert private["initial_memory"] == body["design_markdown"]
    assert "公开目标" in private["goals"]
    assert json.loads((root / "cast/characters/hero/character.json").read_text())["position"] == {
        "row": 2,
        "col": 2,
    }


def test_runtime_snapshot_and_reset_survive_authoring_edits(client):
    client, _ = client
    group(client)
    old = "# 人物\n## 公开目标\n旧的私人愿望。"
    saved = client.put(
        "/api/presets/custom/characters/hero",
        json={"character": character(), "design_markdown": old},
    ).json()
    cfg = client.get("/api/presets/custom/scenario").json()
    cfg["scenario"]["characters"] = [{"group_id": "custom", "character_id": "hero"}]
    client.put(
        "/api/presets/custom/scenario",
        json={"scenario": cfg["scenario"], "expected_revision": cfg["revision"]},
    )
    created = client.post(
        "/api/worlds/from-preset", json={"preset_id": "custom", "mode": "fixture"}
    ).json()
    wid = created["world_id"]
    updated = client.put(
        "/api/presets/custom/characters/hero",
        json={
            "character": character(),
            "design_markdown": "# 新故事",
            "expected_revision": saved["revision"],
        },
    )
    assert updated.status_code == 200
    reset = client.post(
        f"/api/worlds/{wid}/commands", json={"command_id": "reset-design", "kind": "reset"}
    ).json()
    for current in [wid, reset["world_id"]]:
        assert (
            client.get(f"/api/worlds/{current}/perspectives/hero").json()["private"][
                "initial_memory"
            ]
            == old
        )


def test_editor_conflict_readonly_and_private_json_rejection(client):
    client, _ = client
    group(client)
    saved = client.put(
        "/api/presets/custom/characters/hero",
        json={"character": character(), "design_markdown": "# 历史"},
    ).json()
    response = client.put(
        "/api/presets/custom/characters/hero",
        json={"character": character(), "design_markdown": "# 更新", "expected_revision": "stale"},
    )
    assert response.status_code == 409 and response.json()["code"] == "REVISION_CONFLICT"
    assert client.get("/api/presets/custom/characters/hero").json()["revision"] == saved["revision"]
    bad = character()
    bad["public_goal"] = "PRIVATE_TARGET"
    rejected = client.put(
        "/api/presets/custom/characters/hero", json={"character": bad, "design_markdown": "# 身份"}
    )
    assert rejected.status_code == 422 and "PRIVATE_TARGET" not in rejected.text
    assert (
        client.put(
            "/api/presets/the-seal/characters/keeper",
            json={"character": character("keeper"), "design_markdown": "# 修改"},
        ).status_code
        == 409
    )


def test_common_discipline_and_new_ports(client):
    client, _ = client
    rules = client.get("/api/discipline").json()
    assert rules["markdown"] and len(rules["revision"]) == 64
    accepted = client.options(
        "/api/presets/custom/characters/hero",
        headers={
            "Origin": "http://127.0.0.1:5180",
            "Access-Control-Request-Method": "PUT",
            "Access-Control-Request-Headers": "content-type",
        },
    )
    assert accepted.status_code == 200
    denied = client.get("/api/health", headers={"Origin": "http://127.0.0.1:5173"})
    assert denied.status_code == 403


def test_no_key_is_saved_or_echoed_in_character_editor(tmp_path):
    key = "synthetic-" + "K" * 40
    with TestClient(create_app(Settings(data_dir=tmp_path, api_key=SecretStr(key)))) as client:
        group(client)
        response = client.put(
            "/api/presets/custom/characters/hero",
            json={"character": character(), "design_markdown": "# 误粘\n" + key},
        )
        assert response.status_code == 422 and key not in response.text
        assert not (tmp_path / "presets/custom/characters/hero").exists()
