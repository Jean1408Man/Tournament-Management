"""Salud del proceso y disponibilidad real del servicio de torneos."""

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

router = APIRouter(tags=["health"])


@router.get("/health")
def health(request: Request) -> dict:
    return {"status": "alive", "version": request.app.state.package_version}


@router.get("/ready")
def ready(request: Request) -> JSONResponse:
    checks = request.app.state.readiness()
    available = all(value == "ready" for value in checks.values())
    return JSONResponse(
        status_code=200 if available else 503,
        content={
            "status": "ready" if available else "not_ready",
            "scope": "tournament_execution",
            "checks": checks,
        },
    )
