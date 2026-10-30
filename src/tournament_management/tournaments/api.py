"""Crear confirma durabilidad, consultar observa progreso y cancelar cierra aceptación."""

from typing import Annotated

from fastapi import APIRouter, Query, Response

from tournament_management.api.common import ErrorResponse, Identifier
from tournament_management.api.dependencies import ExecutionServices, IdempotencyKey, Services
from tournament_management.tournaments.schemas import (
    CancellationResponse,
    MatchesPage,
    TournamentAccepted,
    TournamentCreate,
    TournamentResponse,
)

router = APIRouter(prefix="/tournaments", tags=["tournaments"])


@router.post(
    "",
    status_code=202,
    response_model=TournamentAccepted,
    responses={
        429: {
            "model": ErrorResponse,
            "description": "Límite de torneos activos alcanzado (code overloaded); no se guardó "
            "nada y la Idempotency-Key no se consumió",
            "headers": {
                "Retry-After": {
                    "description": "Segundos de espera sugeridos",
                    "schema": {"type": "integer"},
                }
            },
        }
    },
)
def create(
    data: TournamentCreate,
    response: Response,
    runtime: ExecutionServices,
    idempotency_key: IdempotencyKey,
) -> dict:
    identifier = runtime.create_tournament(
        idempotency_key=idempotency_key,
        tournament_id=data.tournament_id,
        format_name=data.format,
        competitor_ids=tuple(data.competitor_ids),
        initial_piles=tuple(data.initial_piles),
        first_player=data.first_player,
        seed=data.seed,
    )
    location = f"/api/v1/tournaments/{identifier}"
    response.headers["Location"] = location
    return {"tournament_id": identifier, "status_url": location, "events_url": location + "/events"}


@router.get("/{tournament_id}", response_model=TournamentResponse)
def get(tournament_id: Identifier, runtime: Services) -> dict:
    return runtime.queries.tournament(tournament_id)


@router.get("/{tournament_id}/matches", response_model=MatchesPage)
def matches(
    tournament_id: Identifier,
    runtime: Services,
    offset: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
) -> dict:
    return runtime.queries.matches(tournament_id, offset=offset, limit=limit)


@router.post("/{tournament_id}/cancel", response_model=CancellationResponse)
def cancel(tournament_id: Identifier, runtime: Services) -> dict:
    runtime.tournaments.cancel_tournament(tournament_id)
    if runtime.coordinator is not None:
        runtime.coordinator.cancel(tournament_id)
    return {"tournament_id": tournament_id, "state": runtime.queries.state(tournament_id)}
