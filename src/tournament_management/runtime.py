"""Composición: el único lugar que elige base de datos, almacén y backend de ejecución."""

import logging
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import fields
from tempfile import TemporaryFile

from tournament_management.api.services import ApiServices
from tournament_management.competitors.artifact_store import FileArtifactStore
from tournament_management.competitors.service import CompetitorService
from tournament_management.config import Settings
from tournament_management.database.connection import check_schema
from tournament_management.database.unit_of_work import PostgresUnitOfWork
from tournament_management.execution.backends.local import LocalExecution
from tournament_management.execution.backends.registered import run_registered_match
from tournament_management.execution.coordinator import TournamentCoordinator
from tournament_management.execution.policy import ExecutionPolicy
from tournament_management.tournaments.queries import TournamentQueries
from tournament_management.tournaments.service import (
    PersistentTournamentService,
)

logger = logging.getLogger(__name__)


def check_storage(settings: Settings, *, create: bool = False) -> None:
    path = settings.data_path
    if create:
        path.mkdir(parents=True, exist_ok=True)
    with TemporaryFile(dir=path) as stream:
        stream.write(b"tm-storage-check")
        stream.flush()


@contextmanager
def application_services(settings: Settings) -> Iterator[ApiServices | None]:
    for message in settings.timing_warnings():
        logger.warning(message)
    connection = None
    try:
        if settings.backend == "ray":
            # Una sola conexión por proceso: la abre la composición y la cede
            # al backend, que no importa cluster/. Si falla, no se sirve HTTP.
            from tournament_management.cluster.ray_connection import RayConnection

            connection = RayConnection(settings.ray_address)
            connection.connect()
            logger.info("Conexión Ray Client establecida")
        with _services(settings, connection) as services:
            yield services
    finally:
        if connection is not None:
            connection.close()


def _backend(settings: Settings, store: FileArtifactStore, connection):
    if connection is None:
        return LocalExecution(
            lambda attempt, cancel: run_registered_match(attempt, store, cancel=cancel),
        )
    from tournament_management.execution.backends.ray import RayExecution

    # Los workers descargan los bots del coordinador (4.2): no reciben el almacén.
    return RayExecution(
        connection.snapshot,
        cancel_grace_ms=settings.cancel_grace_ms,
        suspect_cooldown_ms=settings.suspect_cooldown_ms,
    )


@contextmanager
def _services(settings: Settings, connection) -> Iterator[ApiServices | None]:
    if not settings.database_url:
        yield None
        return

    def work_factory():
        return PostgresUnitOfWork(settings.database_url)

    store = FileArtifactStore(
        settings.data_path / "artifacts", max_bytes=settings.artifact_max_bytes
    )
    results = PersistentTournamentService(work_factory)
    services = ApiServices(
        competitors=CompetitorService(work_factory, store, max_bytes=settings.artifact_max_bytes),
        tournaments=results,
        queries=TournamentQueries(work_factory),
        work_factory=work_factory,
        max_upload_bytes=settings.artifact_max_bytes,
        artifacts=store,
        internal_token=settings.internal_token,
    )
    try:
        services.backend = _backend(settings, store, connection)
        policy = ExecutionPolicy(
            **{field.name: getattr(settings, field.name) for field in fields(ExecutionPolicy)}
        )
        services.coordinator = TournamentCoordinator(
            work_factory, services.backend, results, policy
        )
        services.coordinator.start()
        yield services
    finally:
        if services.coordinator:
            services.coordinator.close()
        if services.backend:
            services.backend.close()


def readiness(settings: Settings, services: ApiServices | None) -> dict:
    checks = {"storage": "ready", "database": "not_configured", "execution": "unavailable"}
    try:
        check_storage(settings)
    except OSError:
        checks["storage"] = "unavailable"
    if settings.database_url:
        try:
            check_schema(settings.database_url)
            checks["database"] = "ready"
        except OSError:
            checks["database"] = "unavailable"
    # Con Ray, listo significa conexión abierta y coordinador sano; la falta de
    # workers no lo cambia y se observa en /api/v1/workers (D1.7).
    if services and services.coordinator and services.coordinator.healthy:
        checks["execution"] = "ready"
    return checks
