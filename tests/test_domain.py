import json

import pytest
from chessx.domain.errors import RuleError
from chessx.domain.models import Intent, Scenario
from chessx.domain.profile import RULE_MANIFEST_HASH, frozen_rule_manifest, manifest_hash
from chessx.domain.scenarios import create_world, load_scenario
from chessx.rules.invariants import assert_invariants
from pydantic import TypeAdapter, ValidationError


def scenario_data():
    return {
        "scenario_id": "test",
        "title": "工程场景",
        "board": {"rows": 8, "cols": 8},
        "pieces": [
            {
                "id": "a",
                "name": "A",
                "type": "pawn",
                "color": "白",
                "position": {"row": 3, "col": 3},
                "private_text": "PERSONA_A",
            }
        ],
    }


@pytest.mark.parametrize("rows,cols,count", [(8, 8, 0), (64, 64, 8)])
def test_scenario_boundary_sizes(rows, cols, count):
    data = scenario_data()
    data["board"] = {"rows": rows, "cols": cols}
    data["pieces"] = [{**data["pieces"][0], "id": f"p{i}"} for i in range(count)]
    scenario = Scenario.model_validate(data)
    world = create_world(scenario, {}, "world-1")
    assert len(world["pieces"]) == count
    assert_invariants(world)


@pytest.mark.parametrize(
    "mutate",
    [
        lambda d: d["board"].update(rows=7),
        lambda d: d["board"].update(cols=65),
        lambda d: d["board"].update(rows="8"),
        lambda d: d.update(pieces=d["pieces"] * 9),
        lambda d: d.update(pieces=d["pieces"] * 2),
        lambda d: d["pieces"][0]["position"].update(col=8),
        lambda d: d["pieces"][0].update(type="dragon"),
        lambda d: d["pieces"][0].update(forward_row_delta=1),
        lambda d: d.update(limits={"conversation_max_messages": 51}),
        lambda d: d.update(limits={"battle_max_rounds": 0}),
        lambda d: d["pieces"][0].update(private_file="persona.md"),
        lambda d: d.update(
            objects=[
                {"id": "o", "name": "印信", "location": {"kind": "carried", "holder_id": "unknown"}}
            ]
        ),
        lambda d: d.update(
            objects=[
                {
                    "id": "o",
                    "name": "印信",
                    "location": {
                        "kind": "ground",
                        "position": {"row": 3, "col": 3},
                        "holder_id": "a",
                    },
                }
            ]
        ),
    ],
)
def test_invalid_scenario_rejected(mutate):
    data = scenario_data()
    mutate(data)
    with pytest.raises(ValidationError):
        Scenario.model_validate(data)


def test_persona_loader_is_private_and_confined(tmp_path):
    data = scenario_data()
    data["pieces"][0].pop("private_text")
    data["pieces"][0]["private_file"] = "private/a.md"
    (tmp_path / "private").mkdir()
    (tmp_path / "private/a.md").write_text("PRIVATE_SENTINEL", encoding="utf-8")
    source = tmp_path / "scenario.json"
    source.write_text(json.dumps(data), encoding="utf-8")
    scenario, personas = load_scenario(source)
    world = create_world(scenario, personas, "world")
    assert world["private"]["a"]["persona"] == "PRIVATE_SENTINEL"
    assert "PRIVATE_SENTINEL" not in json.dumps(world["pieces"])
    assert "private_file" not in world["pieces"]["a"]
    assert world["actor_order"] == ["a"]
    assert world["usage"] == {
        "calls": 0,
        "input_tokens": 0,
        "output_tokens": 0,
        "reasoning_tokens": 0,
    }


@pytest.mark.parametrize("relative", ["../outside.md", "/tmp/outside.md"])
def test_loader_rejects_path_escape(tmp_path, relative):
    data = scenario_data()
    data["pieces"][0].pop("private_text")
    data["pieces"][0]["private_file"] = relative
    source = tmp_path / "scenario.json"
    source.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(RuleError, match="within the scenario directory"):
        load_scenario(source)


def test_loader_rejects_symlink_escape(tmp_path):
    directory = tmp_path / "scenario"
    directory.mkdir()
    outside = tmp_path / "outside.md"
    outside.write_text("DO_NOT_LOAD", encoding="utf-8")
    (directory / "linked.md").symlink_to(outside)
    data = scenario_data()
    data["pieces"][0].pop("private_text")
    data["pieces"][0]["private_file"] = "linked.md"
    source = directory / "scenario.json"
    source.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(RuleError) as error:
        load_scenario(source)
    assert error.value.code == "PERSONA_PATH_INVALID"


def test_intents_have_no_unrestricted_fact_changing_fields():
    adapter = TypeAdapter(Intent)
    valid = adapter.validate_python(
        {
            "kind": "move",
            "target": {"row": 3, "col": 2},
            "arrival_action": {"kind": "join_battle", "battle_id": "b1"},
        }
    )
    assert valid.arrival_action.battle_id == "b1"
    for data in [
        {"kind": "attack", "target_id": "b"},
        {"kind": "wait", "kill": "b"},
        {"kind": "move", "target": {"row": "3", "col": 2}},
        {"kind": "change_color", "label": "x" * 33},
    ]:
        with pytest.raises(ValidationError):
            adapter.validate_python(data)


def test_world_construction_does_not_alias_scenario_or_persona_dict():
    scenario = Scenario.model_validate(scenario_data())
    world = create_world(scenario, {"a": "PERSONA"}, "world")
    world["pieces"]["a"]["position"]["row"] = 5
    assert scenario.pieces[0].position.row == 3


def test_initial_goals_extract_only_explicit_personal_goal_sections():
    data = scenario_data()
    data["pieces"][0]["private_text"] = """# A
## 性格与价值观
保守。人生目标是否改变，留待本人解释。
```markdown
## 目标示例不是真的标题
不要提取这段。
```
## 公开目标（尚未广播）
归还印信。
### 下一步
先找到信使。
## 背景
欠信使一条命。
## 隐藏目标
PRIVATE_GOAL_A_93
## 行为风格
言语简短。
"""
    data["pieces"].append(
        {
            "id": "b",
            "name": "B",
            "type": "rook",
            "color": "白",
            "position": {"row": 3, "col": 4},
            "private_text": "# B\n## 目标\nPRIVATE_GOAL_B_46\n",
        }
    )
    scenario = Scenario.model_validate(data)
    state = create_world(scenario, {}, "goals")
    assert (
        state["private"]["a"]["goals"]
        == "## 公开目标（尚未广播）\n归还印信。\n### 下一步\n先找到信使。\n\n## 隐藏目标\nPRIVATE_GOAL_A_93"
    )
    assert state["private"]["a"]["goal_version"] == 0
    assert "PRIVATE_GOAL_B_46" not in state["private"]["a"]["goals"]
    assert "PRIVATE_GOAL_A_93" not in json.dumps(state["pieces"])
    assert state["private"]["a"]["persona"] == data["pieces"][0]["private_text"]


def test_rule_manifest_is_frozen_copied_and_verified_on_world_commit():
    scenario = Scenario.model_validate(scenario_data())
    one = create_world(scenario, {}, "one")
    two = create_world(scenario, {}, "two")
    assert one["rule_manifest_hash"] == RULE_MANIFEST_HASH == manifest_hash(one["rule_manifest"])
    assert len(one["rule_manifest"]["selections"]) == 17
    one["rule_manifest"]["selections"]["R10"] = "neutrals_allowed"
    assert two["rule_manifest"] == frozen_rule_manifest()
    with pytest.raises(RuleError) as error:
        assert_invariants(one)
    assert error.value.code == "INVARIANT_VIOLATION"
    two["rule_manifest_hash"] = "fake"
    with pytest.raises(RuleError):
        assert_invariants(two)
