"""Ensamblado del contrato v1; cada módulo de dominio declara sus propias rutas."""

from fastapi import APIRouter

from tournament_management.api.common import ErrorResponse
from tournament_management.competitors import api as competitors
from tournament_management.events import api as events
from tournament_management.execution import api as workers
from tournament_management.tournaments import api as tournaments

router = APIRouter(
    prefix="/api/v1",
    responses={status: {"model": ErrorResponse} for status in (400, 404, 409, 413, 415, 422, 503)},
)
# El orden fija el del OpenAPI congelado en contracts/openapi-v1.json.
router.include_router(competitors.router)
router.include_router(tournaments.router)
router.include_router(workers.router)
router.include_router(events.router)
