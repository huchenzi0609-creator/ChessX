import json
import os
import shutil
import stat
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
from chessx.presets import PresetError, PresetLibrary


@pytest.fixture
def library(tmp_path):
    return PresetLibrary(tmp_path / "bundled", tmp_path / "local")


def character(character_id="bridge", **updates):
    return {
        "id": character_id,
        "name": "守桥人",
        "type": "rook",
        "color": "gray",
        "position": {"row": 2, "col": 3},
        **updates,
    }


def seed(library, group_id="bridge", character_id="keeper"):
    library.create_group(group_id, "桥边")
    return library.save_character(
        group_id, character_id, character(character_id), "# 私人记忆\n\n我曾经认识信使。"
    )


def expect_error(code, operation):
    with pytest.raises(PresetError) as error:
        operation()
    assert error.value.code == code
    return error.value


def test_empty_library_and_structured_local_creation(library):
    assert library.list_groups() == []
    saved = seed(library)
    group = library.get_group("bridge")
    assert group["id"] == "bridge"
    assert group["characters"] == [
        {"group_id": "bridge", **character("keeper"), "revision": saved["revision"]}
    ]
    assert group["scenario"]["characters"] == []
    assert len(group["scenario_revision"]) == 64
    assert library.list_characters() == group["characters"]
    path = library.local_root / "bridge" / "characters" / "keeper"
    assert json.loads((path / "character.json").read_text()) == character("keeper")
    assert (path / "design.md").read_text() == saved["design_markdown"]
    assert set(path.iterdir()) == {path / "character.json", path / "design.md"}


def test_files_and_directories_are_private(library):
    seed(library)
    for path in [library.local_root, *library.local_root.rglob("*")]:
        mode = stat.S_IMODE(path.stat().st_mode)
        assert mode == (0o700 if path.is_dir() else 0o600)


@pytest.mark.parametrize("bad_id", ["../escape", "a/b", ".hidden", "", "a" * 65, "桥", "a\n"])
def test_identifier_confinement(library, bad_id):
    expect_error("INVALID_ID", lambda: library.create_group(bad_id, "x"))
    expect_error("INVALID_ID", lambda: library.get_group(bad_id))
    library.create_group("safe", "x")
    expect_error("INVALID_ID", lambda: library.get_character("safe", bad_id))
    expect_error(
        "INVALID_ID",
        lambda: library.save_character("safe", bad_id, character(), "# private"),
    )


@pytest.mark.parametrize(
    "private_field", ["public_goal", "hidden_goal", "private_text", "private_file", "relationships"]
)
def test_narrative_fields_are_rejected_in_json(library, private_field):
    library.create_group("safe", "x")
    expect_error(
        "INVALID_DATA",
        lambda: library.save_character(
            "safe", "bridge", character(**{private_field: "SECRET-STORY"}), "# story"
        ),
    )
    assert not (library.local_root / "safe" / "characters" / "bridge").exists()


def test_character_identity_and_utf8_size_limits(library):
    library.create_group("safe", "x")
    expect_error(
        "INVALID_DATA", lambda: library.save_character("safe", "other", character(), "# x")
    )
    for markdown in [" ", "秘" * (262144 // 3 + 1), "\ud800", 42]:
        expect_error(
            "INVALID_DATA",
            lambda text=markdown: library.save_character("safe", "bridge", character(), text),
        )
    saved = library.save_character("safe", "bridge", character(), "a" * 262144)
    assert len(saved["design_markdown"]) == 262144


def test_character_revision_conflict_and_update(library):
    original = seed(library)
    updated = library.save_character(
        "bridge", "keeper", character("keeper", name="新名字"), "# 新记忆", original["revision"]
    )
    assert updated["revision"] != original["revision"]
    expect_error(
        "REVISION_CONFLICT",
        lambda: library.save_character(
            "bridge", "keeper", character("keeper"), "# stale", original["revision"]
        ),
    )
    assert library.get_character("bridge", "keeper") == updated
    expect_error(
        "REVISION_CONFLICT",
        lambda: library.save_character(
            "bridge", "new", character("new"), "# x", original["revision"]
        ),
    )


def test_stale_create_form_cannot_overwrite_existing_character(library):
    library.create_group("bridge", "桥边")
    stale_form = PresetLibrary(library.bundled_root, library.local_root)
    assert stale_form.get_group("bridge")["characters"] == []
    saved = library.save_character(
        "bridge", "keeper", character("keeper"), "# Original private story"
    )
    path = library.local_root / "bridge" / "characters" / "keeper"
    before = {file.name: file.read_bytes() for file in path.iterdir()}
    expect_error(
        "ALREADY_EXISTS",
        lambda: stale_form.save_character(
            "bridge", "keeper", character("keeper", name="Accidental replacement"), "# Lost story"
        ),
    )
    assert library.get_character("bridge", "keeper") == saved
    assert {file.name: file.read_bytes() for file in path.iterdir()} == before
    assert sorted(entry.name for entry in path.parent.iterdir()) == ["keeper"]


def test_simultaneous_character_creates_have_exactly_one_winner(library):
    library.create_group("bridge", "桥边")
    barrier = threading.Barrier(2)

    def create(name):
        independent = PresetLibrary(library.bundled_root, library.local_root)
        barrier.wait(timeout=5)
        try:
            return independent.save_character(
                "bridge", "keeper", character("keeper", name=name), f"# Private story for {name}"
            )
        except PresetError as error:
            return error

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(create, ["First", "Second"]))
    winners = [result for result in results if isinstance(result, dict)]
    conflicts = [result for result in results if isinstance(result, PresetError)]
    assert len(winners) == len(conflicts) == 1
    assert conflicts[0].code == "ALREADY_EXISTS"
    assert library.get_character("bridge", "keeper") == winners[0]


def test_cross_group_reference_alias_position_and_private_separation(library):
    original = seed(library, "source", "keeper")
    seed(library, "destination", "messenger")
    group = library.get_group("destination")
    scenario = group["scenario"]
    scenario["characters"] = [
        {
            "group_id": "source",
            "character_id": "keeper",
            "piece_id": "guest",
            "position": {"row": 4, "col": 5},
        },
        {"group_id": "destination", "character_id": "messenger"},
    ]
    scenario["objects"] = [
        {
            "id": "letter",
            "name": "信",
            "description": "",
            "location": {"kind": "carried", "holder_id": "guest"},
        }
    ]
    saved = library.save_scenario("destination", scenario, group["scenario_revision"])
    built, personas = library.build_scenario("destination")
    assert built.pieces[0].id == "guest"
    assert built.pieces[0].position.row == 4
    assert all(piece.private_text is None and piece.private_file is None for piece in built.pieces)
    assert personas["guest"] == original["design_markdown"]
    assert library.get_character("source", "keeper") == original
    raw = (library.local_root / "destination" / "scenario.json").read_text()
    assert "私人记忆" not in raw and "我曾经" not in raw
    assert "characters" in raw and '"pieces"' not in raw
    expect_error(
        "REVISION_CONFLICT",
        lambda: library.save_scenario("destination", scenario, group["scenario_revision"]),
    )
    assert library.get_group("destination")["scenario_revision"] == saved["revision"]


@pytest.mark.parametrize(
    "invalid",
    [
        "duplicate",
        "out_of_bounds",
        "unknown_holder",
        "missing_character",
        "private_inline",
        "too_many",
    ],
)
def test_invalid_resolved_scenarios_leave_previous_file_intact(library, invalid):
    seed(library)
    old = library.get_group("bridge")
    scenario = old["scenario"]
    ref = {"group_id": "bridge", "character_id": "keeper"}
    scenario["characters"] = [ref]
    code = "INVALID_DATA"
    if invalid == "duplicate":
        scenario["characters"] = [ref, ref]
    elif invalid == "out_of_bounds":
        scenario["characters"][0] = {**ref, "position": {"row": 8, "col": 0}}
    elif invalid == "unknown_holder":
        scenario["objects"] = [
            {"id": "letter", "name": "x", "location": {"kind": "carried", "holder_id": "unknown"}}
        ]
    elif invalid == "missing_character":
        scenario["characters"][0] = {**ref, "character_id": "unknown"}
        code = "NOT_FOUND"
    elif invalid == "private_inline":
        scenario["characters"][0] = {**ref, "private_text": "hidden"}
    else:
        scenario["characters"] = [ref] * 9
    expect_error(code, lambda: library.save_scenario("bridge", scenario))
    current = library.get_group("bridge")
    assert current["scenario_revision"] == old["scenario_revision"]
    assert current["scenario"]["characters"] == []


def test_empty_resolved_scenario(library):
    library.create_group("empty", "空棋盘")
    built, personas = library.build_scenario("empty")
    assert built.pieces == [] and personas == {}


def test_bundled_groups_are_immutable_and_reusable(library):
    source = seed(library)
    library.bundled_root.mkdir()
    shutil.move(library.local_root / "bridge", library.bundled_root / "bridge")
    (library.bundled_root / "bridge" / ".lock").unlink()
    assert library.list_groups()[0]["readonly"] is True
    assert library.get_character("bridge", "keeper") == source
    expect_error(
        "READ_ONLY",
        lambda: library.save_character("bridge", "keeper", character("keeper"), "# changed"),
    )
    expect_error(
        "READ_ONLY",
        lambda: library.save_scenario("bridge", library.get_group("bridge")["scenario"]),
    )
    expect_error("ALREADY_EXISTS", lambda: library.create_group("bridge", "duplicate"))
    assert not (library.bundled_root / "bridge" / ".lock").exists()
    library.create_group("copy", "我的预设")
    library.save_character("copy", "keeper", source["character"], source["design_markdown"])
    assert library.get_character("copy", "keeper")["character"] == source["character"]


def test_symlink_root_group_file_and_character_are_rejected(tmp_path):
    real = tmp_path / "real"
    real.mkdir()
    linked = tmp_path / "linked"
    linked.symlink_to(real, target_is_directory=True)
    expect_error("UNSAFE_PATH", lambda: PresetLibrary(linked, tmp_path / "local"))
    library = PresetLibrary(tmp_path / "bundled", tmp_path / "local")
    seed(library)
    group = library.local_root / "bridge"
    path = group / "characters" / "keeper" / "design.md"
    path.unlink()
    path.symlink_to(real / "private")
    expect_error("UNSAFE_PATH", lambda: library.get_character("bridge", "keeper"))
    expect_error("UNSAFE_PATH", library.list_groups)
    path.unlink()
    path.write_text("# story")
    os.rename(group / "characters" / "keeper", real / "keeper")
    (group / "characters" / "keeper").symlink_to(real / "keeper", target_is_directory=True)
    expect_error("UNSAFE_PATH", lambda: library.get_character("bridge", "keeper"))
    (group / "characters" / "keeper").unlink()
    os.rename(group, real / "bridge")
    group.symlink_to(real / "bridge", target_is_directory=True)
    expect_error("UNSAFE_PATH", lambda: library.get_group("bridge"))


def test_corrupt_group_returns_sanitized_error_instead_of_silent_drop(library):
    seed(library)
    (library.local_root / "bridge" / "group.json").write_text('{"secret":"DO-NOT-ECHO"}')
    error = expect_error("CORRUPT_DATA", library.list_groups)
    assert "DO-NOT-ECHO" not in str(error)
    assert str(library.local_root) not in str(error)


@pytest.mark.parametrize("filename", ["character.json", "design.md", "scenario.json"])
def test_missing_required_files_are_reported(library, filename):
    seed(library)
    group = library.local_root / "bridge"
    path = (
        group / filename
        if filename == "scenario.json"
        else group / "characters" / "keeper" / filename
    )
    path.unlink()
    expect_error("CORRUPT_DATA", library.list_groups)


def test_interrupted_directory_swap_restores_previous_complete_pair(library):
    saved = seed(library)
    directory = library.local_root / "bridge" / "characters"
    os.rename(directory / "keeper", directory / ".keeper.backup")
    pending = directory / (".keeper.pending-" + "a" * 32)
    pending.mkdir()
    (pending / "character.json").write_text('{"incomplete":true}')
    recovered = library.get_character("bridge", "keeper")
    assert recovered == saved
    assert not pending.exists() and not (directory / ".keeper.backup").exists()


def test_interrupted_post_publish_cleanup_keeps_new_complete_pair(library):
    old = seed(library)
    directory = library.local_root / "bridge" / "characters"
    shutil.copytree(directory / "keeper", directory / ".keeper.backup")
    (directory / "keeper" / "design.md").write_text("# 新的完整记忆")
    recovered = library.get_character("bridge", "keeper")
    assert recovered["design_markdown"] == "# 新的完整记忆"
    assert recovered["revision"] != old["revision"]
    assert not (directory / ".keeper.backup").exists()


def test_failed_publish_rolls_back_pair(library, monkeypatch):
    old = seed(library)
    replace = os.replace

    def fail_pending(source, destination):
        if ".pending-" in Path(source).name:
            raise OSError("source content must not be exposed")
        return replace(source, destination)

    monkeypatch.setattr(os, "replace", fail_pending)
    expect_error(
        "IO_ERROR",
        lambda: library.save_character(
            "bridge", "keeper", character("keeper", name="Changed"), "# changed", old["revision"]
        ),
    )
    assert library.get_character("bridge", "keeper") == old


def test_reader_cannot_observe_intermediate_character_pair(library, monkeypatch):
    saved = seed(library)
    replace = os.replace
    staged = threading.Event()
    release = threading.Event()

    def gated_replace(source, destination):
        result = replace(source, destination)
        if Path(destination).name == ".keeper.backup":
            staged.set()
            assert release.wait(5)
        return result

    monkeypatch.setattr(os, "replace", gated_replace)
    with ThreadPoolExecutor(max_workers=2) as pool:
        writer = pool.submit(
            library.save_character,
            "bridge",
            "keeper",
            character("keeper", name="New"),
            "# New",
            saved["revision"],
        )
        assert staged.wait(5)
        other_library = PresetLibrary(library.bundled_root, library.local_root)
        reader = pool.submit(other_library.get_character, "bridge", "keeper")
        assert not reader.done()
        release.set()
        written = writer.result(timeout=5)
        assert reader.result(timeout=5) == written
        assert written["character"]["name"] == "New" and written["design_markdown"] == "# New"


def test_duplicate_group_ids_are_reported(library):
    seed(library)
    library.bundled_root.mkdir()
    shutil.copytree(library.local_root / "bridge", library.bundled_root / "bridge")
    expect_error("CORRUPT_DATA", library.list_groups)
    expect_error("CORRUPT_DATA", lambda: library.get_group("bridge"))
