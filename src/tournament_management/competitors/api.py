"""Subida binaria acotada, sin multipart ni archivos de clientes en rutas ejecutables."""

import io
from dataclasses import asdict
from typing import Annotated

from fastapi import APIRouter, Query, Request, Response
from starlette.concurrency import run_in_threadpool
from starlette.requests import ClientDisconnect

from tournament_management.api.common import Identifier, Name
from tournament_management.api.dependencies import IdempotencyKey, Services
from tournament_management.api.errors import ApiError
from tournament_management.competitors.schemas import CompetitorResponse

router = APIRouter(prefix="/competitors", tags=["competitors"])


@router.post(
    "",
    status_code=201,
    response_model=CompetitorResponse,
    openapi_extra={
        "requestBody": {
            "required": True,
            "content": {
                "application/octet-stream": {"schema": {"type": "string", "format": "binary"}},
            },
        }
    },
)
async def register(
    request: Request,
    response: Response,
    runtime: Services,
    idempotency_key: IdempotencyKey,
    name: Annotated[Name, Query()],
    filename: Annotated[str, Query(min_length=3, max_length=200, pattern=r"\.py$")],
    competitor_id: Annotated[Identifier | None, Query()] = None,
) -> dict:
    if (
        request.headers.get("content-type", "").split(";", 1)[0].strip()
        != "application/octet-stream"
    ):
        raise ApiError(
            415, "unsupported_media_type", "Envía el archivo como application/octet-stream"
        )
    declared = request.headers.get("content-length")
    if declared is not None:
        try:
            length = int(declared)
        except ValueError as exc:
            raise ApiError(400, "invalid_content_length", "Content-Length inválido") from exc
        if length < 0:
            raise ApiError(400, "invalid_content_length", "Content-Length inválido")
        if length > runtime.max_upload_bytes:
            raise ApiError(413, "upload_too_large", "El archivo supera el tamaño permitido")
    content = bytearray()
    try:
        async for chunk in request.stream():
            if len(content) + len(chunk) > runtime.max_upload_bytes:
                raise ApiError(413, "upload_too_large", "El archivo supera el tamaño permitido")
            content.extend(chunk)
    except ClientDisconnect as exc:
        raise ApiError(400, "incomplete_upload", "La subida se interrumpió") from exc
    registered = await run_in_threadpool(
        runtime.competitors.register,
        io.BytesIO(content),
        filename=filename,
        name=name,
        competitor_id=competitor_id,
        idempotency_key=idempotency_key,
    )
    response.headers["Location"] = f"/api/v1/competitors/{registered.competitor_id}"
    return asdict(registered)


@router.get("/{competitor_id}", response_model=CompetitorResponse)
def get_competitor(competitor_id: Identifier, runtime: Services) -> dict:
    return asdict(runtime.competitors.get(competitor_id))
