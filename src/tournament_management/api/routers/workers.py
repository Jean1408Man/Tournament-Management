"""Capacidad observada del backend de ejecución configurado."""

from dataclasses import asdict

from fastapi import APIRouter

from tournament_management.api.dependencies import Services
from tournament_management.api.schemas.workers import WorkerResponse

router = APIRouter(tags=["workers"])


@router.get("/workers", response_model=list[WorkerResponse])
def workers(runtime: Services) -> list[dict]:
    return [asdict(worker) for worker in runtime.backend.workers()] if runtime.backend else []
