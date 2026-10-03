from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


Identifier = Annotated[str, Field(min_length=1, max_length=128)]
PieceType = Literal["king", "queen", "rook", "bishop", "knight", "pawn"]


class Position(StrictModel):
    row: int = Field(ge=0)
    col: int = Field(ge=0)


class Board(StrictModel):
    rows: int = Field(ge=8, le=64)
    cols: int = Field(ge=8, le=64)


class PieceConfig(StrictModel):
    id: Identifier
    name: str = Field(min_length=1, max_length=128)
    type: PieceType
    color: str = Field(min_length=1, max_length=32)
    position: Position
    private_file: str | None = None
    private_text: str | None = None

    @model_validator(mode="after")
    def one_persona_source(self):
        if self.private_file is not None and self.private_text is not None:
            raise ValueError("Specify private_file or private_text, not both")
        return self


class GroundLocation(StrictModel):
    kind: Literal["ground"]
    position: Position


class CarriedLocation(StrictModel):
    kind: Literal["carried"]
    holder_id: Identifier


Location = Annotated[GroundLocation | CarriedLocation, Field(discriminator="kind")]


class ObjectConfig(StrictModel):
    id: Identifier
    name: str = Field(min_length=1, max_length=128)
    description: str = ""
    location: Location


class Limits(StrictModel):
    battle_max_rounds: int = Field(default=4, ge=1)
    conversation_max_messages: int = Field(default=50, ge=1, le=50)


class Scenario(StrictModel):
    schema_version: Literal["2.1", "2.2"] = "2.2"
    scenario_id: Identifier
    title: str = Field(min_length=1, max_length=200)
    board: Board
    rule_profile: Literal["demo-v2.1", "demo-v2.2"] = "demo-v2.2"
    pieces: list[PieceConfig] = Field(default_factory=list, max_length=8)
    objects: list[ObjectConfig] = Field(default_factory=list)
    limits: Limits = Field(default_factory=Limits)

    @model_validator(mode="after")
    def structural_validity(self):
        piece_ids = [piece.id for piece in self.pieces]
        object_ids = [obj.id for obj in self.objects]
        if len(set(piece_ids)) != len(piece_ids):
            raise ValueError("pieces: duplicate id")
        if len(set(object_ids)) != len(object_ids):
            raise ValueError("objects: duplicate id")
        for piece in self.pieces:
            self.check_position(piece.position, f"pieces.{piece.id}.position")
        for obj in self.objects:
            if isinstance(obj.location, GroundLocation):
                self.check_position(obj.location.position, f"objects.{obj.id}.location.position")
            elif obj.location.holder_id not in piece_ids:
                raise ValueError(f"objects.{obj.id}.location.holder_id: unknown piece")
        return self

    def check_position(self, position: Position, path: str):
        if position.row >= self.board.rows or position.col >= self.board.cols:
            raise ValueError(f"{path}: outside board")


class ArrivalAction(StrictModel):
    kind: Literal["join_battle"]
    battle_id: Identifier


class MoveIntent(StrictModel):
    kind: Literal["move"]
    target: Position
    arrival_action: ArrivalAction | None = None


class InviteIntent(StrictModel):
    kind: Literal["invite"]
    target_id: Identifier


class WaitIntent(StrictModel):
    kind: Literal["wait"]


class PickupIntent(StrictModel):
    kind: Literal["pickup"]
    object_id: Identifier


class DropIntent(StrictModel):
    kind: Literal["drop"]
    object_id: Identifier


class TransferIntent(StrictModel):
    kind: Literal["transfer"]
    object_id: Identifier
    target_id: Identifier


class ChangeColorIntent(StrictModel):
    kind: Literal["change_color"]
    label: str = Field(min_length=1, max_length=32)


class JoinBattleIntent(StrictModel):
    kind: Literal["join_battle"]
    battle_id: Identifier


Intent = Annotated[
    MoveIntent
    | InviteIntent
    | WaitIntent
    | PickupIntent
    | DropIntent
    | TransferIntent
    | ChangeColorIntent
    | JoinBattleIntent,
    Field(discriminator="kind"),
]
