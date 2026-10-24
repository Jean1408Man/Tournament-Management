"""Ensamblado del contrato v1; las rutas por recurso viven en routers/."""

from fastapi import APIRouter

from tournament_management.api.routers import competitors, events, tournaments, workers
from tournament_management.api.schemas.common import ErrorResponse

router = APIRouter(
    prefix="/api/v1",
    responses={status: {"model": ErrorResponse} for status in (400, 404, 409, 413, 415, 422, 503)},
)
router.include_router(competitors.router)
router.include_router(tournaments.router)
router.include_router(workers.router)
router.include_router(events.router)
