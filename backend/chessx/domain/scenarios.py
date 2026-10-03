"""Scenario import and world construction, with confined persona-file access."""

import json
import re
from hashlib import sha256
from pathlib import Path

from chessx.domain.errors import RuleError
from chessx.domain.models import Scenario
from chessx.domain.profile import RULE_MANIFEST_HASH, frozen_rule_manifest

PROJECT_ROOT = Path(__file__).resolve().parents[3]
DISCIPLINE_PATH = PROJECT_ROOT / "prompts" / "piece-discipline.md"


def frozen_discipline() -> tuple[str, str]:
    """Snapshot the tracked common instructions, never a character-controlled file."""
    text = DISCIPLINE_PATH.read_text(encoding="utf-8")
    return text, sha256(text.encode("utf-8")).hexdigest()


def initial_goals(persona: str) -> str:
    """Extract only explicitly titled goal sections, preserving their original text."""
    sections = []
    active = []
    level = None
    fence = None
    for line in persona.splitlines():
        marker = re.match(r"^ {0,3}(`{3,}|~{3,})", line)
        if marker:
            token = marker.group(1)
            if fence is None:
                fence = token
            elif token[0] == fence[0] and len(token) >= len(fence):
                fence = None
            if active:
                active.append(line)
            continue
        heading = None if fence else re.match(r"^ {0,3}(#{1,6})\s+(.+?)\s*#*\s*$", line)
        if heading and level is not None and len(heading.group(1)) <= level:
            sections.append("\n".join(active).strip())
            active, level = [], None
        if heading and level is None and "目标" in heading.group(2):
            active = [line]
            level = len(heading.group(1))
        elif active:
            active.append(line)
    if active:
        sections.append("\n".join(active).strip())
    return "\n\n".join(sections)


def load_scenario(
    path: str | Path, *, persona_root: str | Path | None = None
) -> tuple[Scenario, dict[str, str]]:
    path = Path(path).resolve()
    # Bundled legacy scenario descriptors reference the canonical preset designs.
    # Other imports remain confined to their own directory unless the caller
    # deliberately supplies another trusted root. JSON cannot choose that root.
    root = (
        Path(persona_root).resolve()
        if persona_root is not None
        else PROJECT_ROOT / "presets"
        if path.parent == PROJECT_ROOT / "scenarios"
        else path.parent
    )
    scenario = Scenario.model_validate(json.loads(path.read_text(encoding="utf-8")))
    personas: dict[str, str] = {}
    for piece in scenario.pieces:
        if piece.private_file is None:
            personas[piece.id] = piece.private_text or ""
            continue
        relative = Path(piece.private_file)
        if relative.is_absolute() or ".." in relative.parts:
            raise RuleError(
                "PERSONA_PATH_INVALID", "Persona files must stay within the scenario directory"
            )
        resolved = (root / relative).resolve()
        if not resolved.is_relative_to(root):
            raise RuleError(
                "PERSONA_PATH_INVALID", "Persona files must stay within the scenario directory"
            )
        if not resolved.is_file():
            raise RuleError("PERSONA_FILE_MISSING", "A configured persona file is missing")
        personas[piece.id] = resolved.read_text(encoding="utf-8")
    return scenario, personas


def create_world(scenario: Scenario, personas: dict[str, str], world_id: str) -> dict:
    from chessx.runtime.versioning import (
        SAVE_SCHEMA_VERSION,
        execution_policy_hash,
        frozen_execution_policy,
    )

    discipline, discipline_hash = frozen_discipline()
    policy = frozen_execution_policy()
    return {
        "save_schema_version": SAVE_SCHEMA_VERSION,
        "execution_policy": policy,
        "execution_policy_hash": execution_policy_hash(policy),
        "world_id": world_id,
        "scenario_id": scenario.scenario_id,
        "title": scenario.title,
        "rule_version": "2.2",
        "rule_profile": "demo-v2.2",
        "rule_manifest": frozen_rule_manifest("demo-v2.2"),
        "rule_manifest_hash": RULE_MANIFEST_HASH,
        "discipline": discipline,
        "discipline_hash": discipline_hash,
        "board": scenario.board.model_dump(),
        "limits": scenario.limits.model_dump(),
        "actor_order": [piece.id for piece in scenario.pieces],
        "turn": 0,
        "completed_turn": 0,
        "seq": 0,
        "generation": 0,
        "lifecycle": "CREATED",
        "phase": "START",
        "cursor": {},
        "pieces": {
            piece.id: {
                **piece.model_dump(exclude={"private_file", "private_text"}),
                "alive": True,
                "rest_remaining": 0,
                "conversation_id": None,
                "battle_id": None,
            }
            for piece in scenario.pieces
        },
        "objects": {obj.id: obj.model_dump() for obj in scenario.objects},
        "tombstones": {},
        "conversations": {},
        "battles": {},
        "transfer_offers": {},
        "receipts": {piece.id: [] for piece in scenario.pieces},
        "private": {
            piece.id: {
                "persona": personas.get(piece.id, piece.private_text or ""),
                "initial_memory": personas.get(piece.id, piece.private_text or ""),
                "memory": "",
                "goals": initial_goals(personas.get(piece.id, piece.private_text or "")),
                "memory_version": 0,
                "rolling_summary": "",
                "compaction_watermark": 0,
                "long_term_memory": {},
                "spatial_memory": {"schema_version": 1, "seen_people": {}},
                "goal_version": 0,
            }
            for piece in scenario.pieces
        },
        "stories": {},
        "segments": [],
        "usage": {"calls": 0, "input_tokens": 0, "output_tokens": 0, "reasoning_tokens": 0},
    }
