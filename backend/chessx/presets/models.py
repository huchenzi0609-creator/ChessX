"""On-disk preset contracts; character narrative lives exclusively in Markdown."""

from typing import Annotated, Literal

from pydantic import Field

from chessx.domain.models import (
    Board,
    Limits,
    ObjectConfig,
    PieceType,
    Position,
    StrictModel,
)

SafeIdentifier = Annotated[str, Field(pattern=r"^[a-zA-Z0-9_-]{1,64}$")]


class GroupMetadata(StrictModel):
    id: SafeIdentifier
    title: str = Field(min_length=1, max_length=200)
    description: str = Field(default="", max_length=16384)


class CharacterInformation(StrictModel):
    """Only visible, mechanical attributes may be stored in character.json."""

    id: SafeIdentifier
    name: str = Field(min_length=1, max_length=128)
    type: PieceType
    color: str = Field(min_length=1, max_length=32)
    position: Position


class CharacterReference(StrictModel):
    group_id: SafeIdentifier
    character_id: SafeIdentifier
    piece_id: SafeIdentifier | None = None
    position: Position | None = None


class PresetScenario(StrictModel):
    schema_version: Literal["2.1"] = "2.1"
    scenario_id: SafeIdentifier
    title: str = Field(min_length=1, max_length=200)
    board: Board
    rule_profile: Literal["demo-v2.1"] = "demo-v2.1"
    characters: list[CharacterReference] = Field(default_factory=list, max_length=8)
    objects: list[ObjectConfig] = Field(default_factory=list, max_length=1024)
    limits: Limits = Field(default_factory=Limits)
