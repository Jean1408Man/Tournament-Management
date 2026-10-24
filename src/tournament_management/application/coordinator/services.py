"""Servicios que recibe la interfaz HTTP mediante composición explícita."""

from collections.abc import Callable
from dataclasses import dataclass
from uuid import NAMESPACE_URL, uuid5

from tournament_management.application.competitor_service import CompetitorService
from tournament_management.application.coordinator.engine import TournamentCoordinator
from tournament_management.application.coordinator.queries import TournamentQueries
from tournament_management.application.persistent_tournament_service import (
    PersistentTournamentService,
)
from tournament_management.application.ports import ExecutionBackend, UnitOfWork
from tournament_management.domain.persistent_tournament import TournamentSpec


@dataclass
class ApiServices:
    competitors: CompetitorService
    tournaments: PersistentTournamentService
    queries: TournamentQueries
    work_factory: Callable[[], UnitOfWork]
    max_upload_bytes: int
    backend: ExecutionBackend | None = None
    coordinator: TournamentCoordinator | None = None

    def create_tournament(
        self,
        *,
        idempotency_key: str,
        tournament_id: str | None,
        format_name: str,
        competitor_ids: tuple[str, ...],
        initial_piles: tuple[int, ...],
        first_player: int,
        seed: int,
    ) -> str:
        # La API resuelve los hashes desde el registro; el cliente no puede
        # sustituir código ni enviar un comando de ejecución dentro del torneo.
        registered = tuple(self.competitors.get(identifier) for identifier in competitor_ids)
        identifier = tournament_id or str(
            uuid5(NAMESPACE_URL, f"tm:http:tournament:{idempotency_key}")
        )
        spec = TournamentSpec(
            tournament_id=identifier,
            format_name=format_name,
            competitor_ids=competitor_ids,
            artifact_hashes=tuple(item.artifact.artifact_hash for item in registered),
            initial_piles=initial_piles,
            first_player=first_player,
            seed=seed,
        )
        identifier = self.tournaments.create(spec, idempotency_key=idempotency_key)
        # La cola se toca únicamente después del commit. Repetir una creación
        # confirma el mismo recurso; un torneo terminal no vuelve a ejecutarse.
        self.coordinator.enqueue(identifier)
        return identifier
