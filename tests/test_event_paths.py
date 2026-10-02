"""Full observer movement paths and clipped, least-privilege witness receipts."""

from copy import deepcopy

from chessx.config import Settings
from chessx.contexts import project_event
from chessx.domain.models import Scenario
from chessx.domain.scenarios import create_world
from chessx.rules.geometry import commit_move
from chessx.runtime.runner import WorldRunner
from chessx.storage import Store


def state(kind="rook"):
    scenario = Scenario.model_validate(
        {
            "scenario_id": "paths",
            "title": "Paths",
            "board": {"rows": 8, "cols": 8},
            "pieces": [
                {
                    "id": "mover",
                    "name": "移动者",
                    "type": kind,
                    "color": "白",
                    "position": {"row": 0, "col": 0},
                },
                {
                    "id": "witness",
                    "name": "见证者",
                    "type": "king",
                    "color": "白",
                    "position": {"row": 3, "col": 1},
                },
                {
                    "id": "blind",
                    "name": "远处",
                    "type": "king",
                    "color": "白",
                    "position": {"row": 7, "col": 7},
                },
            ],
        }
    )
    return create_world(scenario, {}, "paths")


def test_recorded_path_is_actual_travel_and_horse_has_no_middle_squares():
    world = state()
    event = commit_move(world, "mover", {"row": 4, "col": 0})[0]
    assert event["payload"]["path"] == [{"row": r, "col": 0} for r in range(5)]
    horse = state("knight")
    jump = commit_move(horse, "mover", {"row": 2, "col": 1})[0]
    assert jump["payload"]["path"] == [{"row": 0, "col": 0}, {"row": 2, "col": 1}]


def test_witness_gets_only_seen_segment_not_hidden_origin_destination_or_rest():
    world = state()
    event = commit_move(world, "mover", {"row": 7, "col": 0})[0]
    event["audience"] = ["mover", "witness"]
    owner = project_event(world, event, "mover")["payload"]
    seen = project_event(world, event, "witness")["payload"]
    assert owner["path"] == [{"row": r, "col": 0} for r in range(8)]
    assert owner["origin"] == {"row": 0, "col": 0} and owner["position"] == {"row": 7, "col": 0}
    assert seen["path"] == [{"row": r, "col": 0} for r in (2, 3, 4)]
    assert seen["path_visible_only"] and seen["piece_name"] == "移动者"
    assert "origin" not in seen and "position" not in seen and "rest_remaining" not in seen
    assert project_event(world, event, "blind") is None


def test_crossing_a_visible_area_delivers_clipped_path_at_commit(tmp_path):
    before = state()
    world = deepcopy(before)
    event = commit_move(world, "mover", {"row": 7, "col": 0})[0]
    store = Store(tmp_path / "paths.sqlite3")
    store.create(before)
    runner = WorldRunner(store, Settings(data_dir=tmp_path))
    runner._audiences(before, world, [event])
    assert "witness" in event["audience"] and "blind" not in event["audience"]
    assert project_event(world, event, "witness")["payload"]["path_visible_only"]
    store.close()
