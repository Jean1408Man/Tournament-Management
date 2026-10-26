"""Factoría HTTP y ciclo de vida. La composición concreta vive en runtime.py."""

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI

from tournament_management import __version__
from tournament_management.api.errors import install_error_handlers, storage_error
from tournament_management.api.health import router as health_router
from tournament_management.api.routes import router as api_router
from tournament_management.config import Settings
from tournament_management.database.connection import driver
from tournament_management.runtime import application_services, readiness
from tournament_management.runtime import check_storage as check_storage

logger = logging.getLogger(__name__)


def create_app(settings: Settings) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        check_storage(settings, create=True)
        connection = None
        try:
            if settings.backend == "ray":
                from tournament_management.cluster.ray_connection import RayConnection

                connection = RayConnection(settings.ray_address)
                connection.connect()
                logger.info("Conexión Ray Client establecida")
            if settings.database_url:
                from tournament_management.database.connection import check_schema

                check_schema(settings.database_url)
                logger.info("Conexión PostgreSQL comprobada")
            with application_services(settings) as services:
                app.state.services = services
                logger.info("Servicio HTTP iniciado")
                yield
        finally:
            app.state.services = None
            if connection is not None:
                connection.close()
            logger.info("Servicio detenido limpiamente")

    app = FastAPI(
        title="Tournament Management API",
        version="1.0.0",
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
    return app
