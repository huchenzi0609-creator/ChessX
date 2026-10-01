"""Markdown role anchors, shared discipline snapshots and audience boundaries."""

import json
from hashlib import sha256
from pathlib import Path

import pytest
from chessx.contexts import bard_context, battle_context, build_context, observe
from chessx.contexts.budgets import fit_context
from chessx.domain.errors import RuleError
from chessx.domain.models import Scenario
from chessx.domain.scenarios import create_world, load_scenario
from chessx.llm import ModelError
from chessx.llm.validation import TokenCounter

ROOT = Path(__file__).resolve().parents[1]


def character(identifier, col):
    return {
        "id": identifier,
        "name": identifier,
        "type": "rook",
        "color": "青",
        "position": {"row": 0, "col": col},
    }


def world():
    scenario = Scenario.model_validate(
        {
            "scenario_id": "private-design",
            "title": "人物私有资料",
            "board": {"rows": 8, "cols": 8},
            "pieces": [character("a", 0), character("b", 1)],
        }
    )
    return create_world(
        scenario,
        {
            "a": "# A\n## 过往故事\nA_PRIVATE_LIFE\n## 公开目标\nA_PUBLIC_GOAL_PRIVATE\n",
            "b": "# B\n## 过往故事\nB_PRIVATE_LIFE\n## 隐藏目标\nB_SECRET_GOAL\n",
        },
        "markdown-world",
    )


def test_shared_discipline_frozen_and_private_markdown_injected_only_once():
    state = world()
    context = build_context(state, "a", "action")
    assert context["discipline"] == state["discipline"]
    assert sha256(context["discipline"].encode()).hexdigest() == state["discipline_hash"]
    assert context["persona"] == state["private"]["a"]["initial_memory"]
    assert "initial_memory" not in context
    assert context["memory"] == ""
    encoded = json.dumps(context, ensure_ascii=False)
    assert encoded.count("A_PRIVATE_LIFE") == 1
    assert "B_PRIVATE_LIFE" not in encoded
    assert "B_SECRET_GOAL" not in encoded
    assert "public goals remain private" in context["context_channels"]["goals"]
    assert "公开目标" in context["discipline"]


def test_old_world_context_falls_back_to_common_discipline_without_other_roles():
    state = world()
    state.pop("discipline")
    state.pop("discipline_hash")
    context = build_context(state, "a", "memory")
    assert context["discipline"]
    assert sha256(context["discipline"].encode()).hexdigest() == context["discipline_hash"]
    assert "B_PRIVATE_LIFE" not in json.dumps(context)


def test_saved_world_keeps_discipline_snapshot_instead_of_current_file(monkeypatch):
    state = world()
    monkeypatch.setattr(
        "chessx.contexts.builders.frozen_discipline", lambda: ("CHANGED_FILE", "changed-hash")
    )
    context = build_context(state, "a", "action")
    assert context["discipline"] == state["discipline"]
    assert context["discipline_hash"] == state["discipline_hash"]


def test_openly_expressible_goal_is_private_until_speech():
    state = world()
    assert "A_PUBLIC_GOAL_PRIVATE" not in json.dumps(state["pieces"])
    assert "A_PUBLIC_GOAL_PRIVATE" not in json.dumps(observe(state, "b"))
    assert "A_PUBLIC_GOAL_PRIVATE" not in json.dumps(build_context(state, "b", "action"))
    assert "A_PUBLIC_GOAL_PRIVATE" in build_context(state, "a", "action")["goals"]


def test_narrator_has_labeled_initial_and_dynamic_memories_but_bard_has_neither():
    state = world()
    state["private"]["a"]["memory"] = "A_NEW_MEMORY"
    state["private"]["b"]["memory"] = "B_NEW_MEMORY"
    context = battle_context(state, {"id": "battle", "participant_ids": ["a"]})
    assert context["participant_memories"] == [
        {
            "owner_id": "a",
            "memory": state["private"]["a"]["initial_memory"] + "\n\nA_NEW_MEMORY",
        }
    ]
    assert "B_PRIVATE_LIFE" not in json.dumps(context)
    assert "B_NEW_MEMORY" not in json.dumps(context)
    assert "Do not publish private recollections" in context["instruction"]
    assert "A_PRIVATE_LIFE" not in json.dumps(bard_context(state, 0, 5, []))


def test_explicit_trusted_persona_root_with_nested_markdown(tmp_path):
    imported = tmp_path / "imports"
    trusted = tmp_path / "presets"
    imported.mkdir()
    nested = trusted / "group/characters/a"
    nested.mkdir(parents=True)
    (nested / "design.md").write_text("PRIVATE_ROOT_DESIGN", encoding="utf-8")
    data = {
        "scenario_id": "test",
        "title": "test",
        "board": {"rows": 8, "cols": 8},
        "pieces": [{**character("a", 0), "private_file": "group/characters/a/design.md"}],
    }
    path = imported / "scenario.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    _, designs = load_scenario(path, persona_root=trusted)
    assert designs == {"a": "PRIVATE_ROOT_DESIGN"}
    with pytest.raises(RuleError) as error:
        load_scenario(path)
    assert error.value.code == "PERSONA_FILE_MISSING"
    data["pieces"][0]["private_file"] = "../outside.md"
    path.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(RuleError) as error:
        load_scenario(path, persona_root=trusted)
    assert error.value.code == "PERSONA_PATH_INVALID"


def test_explicit_persona_root_rejects_symlink_escape(tmp_path):
    trusted = tmp_path / "trusted"
    trusted.mkdir()
    outside = tmp_path / "outside.md"
    outside.write_text("DO_NOT_READ", encoding="utf-8")
    (trusted / "design.md").symlink_to(outside)
    path = tmp_path / "scenario.json"
    path.write_text(
        json.dumps(
            {
                "scenario_id": "test",
                "title": "test",
                "board": {"rows": 8, "cols": 8},
                "pieces": [{**character("a", 0), "private_file": "design.md"}],
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(RuleError) as error:
        load_scenario(path, persona_root=trusted)
    assert error.value.code == "PERSONA_PATH_INVALID"


@pytest.mark.parametrize("group_id", ["the-seal", "strangers"])
def test_bundled_seed_designs_have_separate_structural_files_and_full_markdown(group_id):
    scenario, designs = load_scenario(ROOT / "scenarios" / f"{group_id}.json")
    for piece in scenario.pieces:
        directory = ROOT / "presets" / group_id / "characters" / piece.id
        structural = json.loads((directory / "character.json").read_text(encoding="utf-8"))
        assert set(structural) == {"id", "name", "type", "color", "position"}
        assert designs[piece.id] == (directory / "design.md").read_text(encoding="utf-8")
        assert len(designs[piece.id]) > 800
        for heading in (
            "## 身份与过往故事",
            "## 性格与价值观",
            "## 公开目标",
            "## 隐藏目标",
            "## 与其他人的关系",
            "## 对其他人的初始看法",
            "## 行为风格",
            "## 初始知识与记忆边界",
        ):
            assert heading in designs[piece.id]


@pytest.mark.parametrize("field", ["persona", "discipline"])
def test_long_essential_design_or_discipline_pauses_instead_of_truncating(field):
    context = build_context(world(), "a", "action")
    context[field] = "ESSENTIAL_PRIVATE_ANCHOR " * 18000
    with pytest.raises(ModelError) as error:
        fit_context(
            "action",
            context,
            {},
            TokenCounter("cl100k_base"),
            max_output_tokens=256,
            input_limit=8192,
        )
    assert error.value.code == "CONTEXT_BUDGET_EXCEEDED"
    assert "ESSENTIAL_PRIVATE_ANCHOR" not in error.value.message
    assert context[field].count("ESSENTIAL_PRIVATE_ANCHOR") == 18000


def test_configured_character_input_budget_preserves_rich_role_anchor():
    counter = TokenCounter("cl100k_base")
    context = {"persona": "history " * 4500}
    fitted = fit_context("action", context, {}, counter, max_output_tokens=128, input_limit=8192)
    assert fitted == context
    with pytest.raises(ModelError):
        fit_context("action", context, {}, counter, max_output_tokens=128)
