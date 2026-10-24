"""Peticiones de creación y vistas del torneo; los límites los fija el servidor."""

from typing import Annotated, Literal

from pydantic import BaseModel, Field, field_validator

from tournament_management.api.schemas.common import Identifier, StrictBody

State = Literal["created", "running", "completed", "blocked", "cancelled"]
Pile = Annotated[int, Field(strict=True, gt=0, le=10000)]


class TournamentCreate(StrictBody):
    tournament_id: Identifier | None = None
    format: Literal["elimination", "round_robin"]
    competitor_ids: Annotated[list[Identifier], Field(min_length=2, max_length=64)]
    initial_piles: Annotated[list[Pile], Field(min_length=1, max_length=64)] = Field(
        default_factory=lambda: [3, 5, 7],
    )
    first_player: Annotated[int, Field(strict=True, ge=0, le=1)] = 0
    seed: Annotated[int, Field(strict=True, ge=0, le=2**63 - 1)] = 42

    @field_validator("competitor_ids")
    @classmethod
    def distinct_participants(cls, identifiers: list[str]) -> list[str]:
        if len(set(identifiers)) != len(identifiers):
            raise ValueError("Los participantes deben ser distintos")
        return identifiers


class TournamentAccepted(BaseModel):
    tournament_id: str
    status_url: str
    events_url: str


class Statistics(BaseModel):
    matches_total: int
    attempts_total: int
    waiting: int
    ready: int
    running: int
    completed: int
    blocked: int
    cancelled: int


class Standing(BaseModel):
    competitor_id: str
    wins: int


class BracketEntry(BaseModel):
    pairing_id: str
    round_number: int
    match_id: str
    competitor_ids: list[str]
    state: Literal["waiting", "ready", "running", "completed", "blocked", "cancelled"]
    winner_id: str | None


class TournamentResponse(BaseModel):
    tournament_id: str
    state: State
    format: Literal["elimination", "round_robin"]
    competitor_ids: list[str]
    statistics: Statistics
    bracket: list[BracketEntry]
    champion: str | None
    standings: list[Standing]


class CancellationResponse(BaseModel):
    tournament_id: str
    state: State
