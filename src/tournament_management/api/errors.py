"""Errores uniformes sin detalles SQL, rutas internas ni cuerpos subidos."""

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from tournament_management.competitors.artifact_store import ArtifactIntegrityError
from tournament_management.shared.errors import ConflictError


class ApiError(Exception):
    def __init__(self, status: int, code: str, message: str) -> None:
        self.status = status
        self.code = code
        self.message = message


def error_response(status: int, code: str, message: str) -> JSONResponse:
    return JSONResponse(status_code=status, content={"error": {"code": code, "message": message}})


async def storage_error(request: Request, exc: Exception) -> JSONResponse:
    return error_response(503, "storage_unavailable", "El almacenamiento no está disponible")


def install_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(ApiError)
    async def api_error(request: Request, exc: ApiError):
        return error_response(exc.status, exc.code, exc.message)

    @app.exception_handler(ConflictError)
    async def conflict(request: Request, exc: ConflictError):
        return error_response(409, "conflict", str(exc))

    @app.exception_handler(KeyError)
    async def missing(request: Request, exc: KeyError):
        return error_response(404, "not_found", "El recurso no existe o no está disponible")

    @app.exception_handler(ValueError)
    async def invalid(request: Request, exc: ValueError):
        return error_response(422, "invalid_input", str(exc))

    @app.exception_handler(RequestValidationError)
    async def validation(request: Request, exc: RequestValidationError):
        # No devolver input/ctx: pueden contener el ejecutable o datos sensibles.
        return error_response(422, "invalid_request", "Revisa los campos y tipos de la petición")

    @app.exception_handler(ArtifactIntegrityError)
    async def artifact_error(request: Request, exc: ArtifactIntegrityError):
        return error_response(503, "artifact_unavailable", "No se pudo verificar el artefacto")

    app.add_exception_handler(OSError, storage_error)
