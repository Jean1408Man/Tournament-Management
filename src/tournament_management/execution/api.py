"""Capacidad observada del backend de ejecución configurado."""

from dataclasses import asdict
from typing import Literal

from fastapi import APIRouter
from pydantic import BaseModel

from tournament_management.api.dependencies import Services


# Identidad y capacidad propias, sin exponer referencias de Ray. Comentario y no
# docstring: Pydantic publicaría el docstring en el OpenAPI congelado.
class WorkerResponse(BaseModel):
    schema_version: Literal[1]
    worker_id: str
    status: Literal["available", "suspected", "lost"]
    capacity: int
    running_attempts: int


router = APIRouter(tags=["workers"])


@router.get("/workers", response_model=list[WorkerResponse])
def workers(runtime: Services) -> list[dict]:
    return [asdict(worker) for worker in runtime.backend.workers()] if runtime.backend else []
