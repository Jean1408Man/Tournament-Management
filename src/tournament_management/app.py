"""Composición del servicio mínimo; todavía no ejecuta torneos."""

import logging
from contextlib import asynccontextmanager
from tempfile import TemporaryFile

from fastapi import FastAPI
from fastapi.responses import JSONResponse

from tournament_management import __version__
from tournament_management.config import Settings

logger = logging.getLogger(__name__)


def check_storage(settings: Settings, *, create: bool = False) -> None:
    """Prueba real de escritura temporal; no crea una base de datos."""
    path = settings.data_path
    if create:
        path.mkdir(parents=True, exist_ok=True)
    with TemporaryFile(dir=path) as stream:
        stream.write(b"tm-storage-check")
        stream.flush()


def create_app(settings: Settings) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        check_storage(settings, create=True)
        connection = None
        try:
            if settings.backend == "ray":
                from tournament_management.adapters.ray_membership import RayConnection

                connection = RayConnection(settings.ray_address)
                connection.connect()
                logger.info("Conexión Ray Client establecida")
            if settings.database_url:
                from tournament_management.adapters.postgres_store import check_connection

                check_connection(settings.database_url)
                logger.info("Conexión PostgreSQL comprobada")
            logger.info("Servicio mínimo iniciado")
            yield
        finally:
            if connection is not None:
                connection.close()
            logger.info("Servicio detenido limpiamente")

    app = FastAPI(title="Tournament Management", version=__version__, lifespan=lifespan)

    @app.get("/health")
    def health():
        return {"status": "alive", "version": __version__}

    @app.get("/ready")
    def ready():
        checks = {"storage": "ready", "database": "not_configured", "execution": "not_implemented"}
        try:
            check_storage(settings)
        except OSError:
            checks["storage"] = "unavailable"
        if settings.database_url:
            from tournament_management.adapters.postgres_store import check_connection

            try:
                check_connection(settings.database_url)
                checks["database"] = "ready"
            except OSError:
                checks["database"] = "unavailable"
        # Hasta integrar los backends, salud HTTP no implica capacidad de torneos.
        return JSONResponse(
            status_code=503,
            content={
                "status": "not_ready",
                "scope": "tournament_execution",
                "checks": checks,
            },
        )

    return app
