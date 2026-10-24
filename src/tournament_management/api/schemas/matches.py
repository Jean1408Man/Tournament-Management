"""Partidas e historial de intentos, paginados para limitar cada respuesta."""

from typing import Literal

from pydantic import BaseModel


class MatchSpecResponse(BaseModel):
    schema_version: Literal[1]
    tournament_id: str
    match_id: str
    competitor_ids: list[str]
    artifact_hashes: list[str]
    initial_piles: list[int]
    first_player: int
    seed: int


class OutcomeResponse(BaseModel):
    schema_version: Literal[1]
    match_id: str
    attempt_id: str
    generation: int
    worker_id: str
    kind: Literal["completed", "bot_failure", "infrastructure_failure", "no_winner"]
    winner_id: str
    reason: str


class AttemptResponse(BaseModel):
    attempt_id: str
    generation: int
    state: Literal[
        "queued", "running", "accepted", "failed", "lost", "expired", "discarded", "cancelled"
    ]
    outcome: OutcomeResponse | None


class MatchResponse(BaseModel):
    match_id: str
    state: Literal["waiting", "ready", "running", "completed", "blocked", "cancelled"]
    spec: MatchSpecResponse
    winner_id: str | None
    current_attempt_id: str | None
    accepted_attempt_id: str | None
    attempts: list[AttemptResponse]


class MatchesPage(BaseModel):
    items: list[MatchResponse]
    total: int
    offset: int
    limit: int
