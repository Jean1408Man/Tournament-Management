"""Composición de adaptadores; aplicación y API no importan infraestructura."""

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import fields
from tempfile import TemporaryFile

from tournament_management.adapters.artifact_store import FileArtifactStore
from tournament_management.adapters.local_execution import LocalExecution
from tournament_management.adapters.postgres_connection import check_schema
from tournament_management.adapters.postgres_store import PostgresUnitOfWork
from tournament_management.adapters.registered_execution import run_registered_match
from tournament_management.application.competitor_service import CompetitorService
from tournament_management.application.coordinator.engine import TournamentCoordinator
from tournament_management.application.coordinator.policy import ExecutionPolicy
from tournament_management.application.coordinator.queries import TournamentQueries
from tournament_management.application.coordinator.services import ApiServices
from tournament_management.application.persistent_tournament_service import (
    PersistentTournamentService,
)
from tournament_management.config import Settings


def check_storage(settings: Settings, *, create: bool = False) -> None:
    path = settings.data_path
    if create:
        path.mkdir(parents=True, exist_ok=True)
    with TemporaryFile(dir=path) as stream:
        stream.write(b"tm-storage-check")
        stream.flush()


@contextmanager
def application_services(settings: Settings) -> Iterator[ApiServices | None]:
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
    )
    try:
        if settings.backend == "local":
            services.backend = LocalExecution(
                lambda attempt, cancel: run_registered_match(attempt, store, cancel=cancel),
            )
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
    if services and services.coordinator and services.coordinator.healthy:
        checks["execution"] = "ready"
    elif settings.backend == "ray":
        checks["execution"] = "not_implemented"
    return checks
