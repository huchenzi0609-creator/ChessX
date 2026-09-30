"""Scenario import and world construction, with confined persona-file access."""

import json
import re
from pathlib import Path

from chessx.domain.errors import RuleError
from chessx.domain.models import Scenario
from chessx.domain.profile import RULE_MANIFEST_HASH, frozen_rule_manifest


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


def load_scenario(path: str | Path) -> tuple[Scenario, dict[str, str]]:
    path = Path(path).resolve()
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
        resolved = (path.parent / relative).resolve()
        if not resolved.is_relative_to(path.parent):
            raise RuleError(
                "PERSONA_PATH_INVALID", "Persona files must stay within the scenario directory"
            )
        if not resolved.is_file():
            raise RuleError("PERSONA_FILE_MISSING", "A configured persona file is missing")
        personas[piece.id] = resolved.read_text(encoding="utf-8")
    return scenario, personas


def create_world(scenario: Scenario, personas: dict[str, str], world_id: str) -> dict:
    return {
        "world_id": world_id,
        "scenario_id": scenario.scenario_id,
        "title": scenario.title,
        "rule_version": "2.1",
        "rule_profile": "demo-v2.1",
        "rule_manifest": frozen_rule_manifest(),
        "rule_manifest_hash": RULE_MANIFEST_HASH,
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
                "memory": "",
                "goals": initial_goals(personas.get(piece.id, piece.private_text or "")),
                "memory_version": 0,
                "goal_version": 0,
            }
            for piece in scenario.pieces
        },
        "stories": {},
        "segments": [],
        "usage": {"calls": 0, "input_tokens": 0, "output_tokens": 0, "reasoning_tokens": 0},
    }
