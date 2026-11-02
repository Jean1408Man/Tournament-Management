"""Factoría HTTP y ciclo de vida. La composición concreta vive en runtime.py."""

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI

from tournament_management import __version__
from tournament_management.api.errors import install_error_handlers, storage_error
from tournament_management.api.health import router as health_router
from tournament_management.api.routes import router as api_router
from tournament_management.competitors.api import internal_router as internal_artifacts_router
from tournament_management.config import Settings
from tournament_management.database.connection import driver
from tournament_management.runtime import application_services, readiness
from tournament_management.runtime import check_storage as check_storage

logger = logging.getLogger(__name__)


def create_app(settings: Settings) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        check_storage(settings, create=True)
        try:
            if settings.database_url:
                from tournament_management.database.connection import check_schema

                check_schema(settings.database_url)
                logger.info("Conexión PostgreSQL comprobada")
            # runtime.py abre y cierra la conexión Ray junto con el backend.
            with application_services(settings) as services:
                app.state.services = services
                logger.info("Servicio HTTP iniciado")
                yield
        finally:
            app.state.services = None
            logger.info("Servicio detenido limpiamente")

    app = FastAPI(
        title="Tournament Management API",
        version="1.1.0",
        lifespan=lifespan,
        openapi_url="/api/v1/openapi.json",
    )
    app.state.services = None
    app.state.package_version = __version__
    app.state.readiness = lambda: readiness(settings, app.state.services)
    install_error_handlers(app)
    psycopg, _ = driver()
    app.add_exception_handler(psycopg.Error, storage_error)
    app.include_router(health_router)
    app.include_router(api_router)
    app.include_router(internal_artifacts_router)
    return app
