"""Dependencias HTTP: la composición se realiza fuera de la capa API."""

from typing import Annotated

from fastapi import Depends, Header, Request

from tournament_management.api.errors import ApiError
from tournament_management.application.coordinator.services import ApiServices

IdempotencyKey = Annotated[
    str,
    Header(
        alias="Idempotency-Key",
        min_length=1,
        max_length=128,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]*$",
    ),
]


def services(request: Request) -> ApiServices:
    runtime = request.app.state.services
    if runtime is None:
        raise ApiError(503, "storage_unavailable", "Se requiere PostgreSQL configurado y migrado")
    return runtime


Services = Annotated[ApiServices, Depends(services)]


def execution(runtime: Services) -> ApiServices:
    if runtime.coordinator is None or not runtime.coordinator.healthy:
        raise ApiError(503, "execution_unavailable", "La ejecución local no está disponible")
    return runtime


ExecutionServices = Annotated[ApiServices, Depends(execution)]
