"""Puertos sin referencias Ray; no implementan ejecución ni persistencia."""

from typing import Protocol, Self

from tournament_management.domain.competitors import RegisteredCompetitor
from tournament_management.domain.messages import (
    AttemptOutcome,
    AttemptSpec,
    DomainEvent,
    MatchSpec,
    WorkerInfo,
)
from tournament_management.domain.persistent_tournament import TournamentSpec
from tournament_management.domain.states import AttemptState, MatchState, TournamentState


class ArtifactIntegrityError(OSError):
    """Objeto ausente, ilegible o cuyo contenido no coincide con su hash."""


class ArtifactStore(Protocol):
    def put(self, content: bytes) -> str:
        """Publica bytes completos sin sobrescribir y retorna su SHA-256."""
        ...

    def get(self, artifact_hash: str) -> bytes:
        """Lee y verifica integridad en cada acceso, también en los workers."""
        ...


class ExecutionError(Exception):
    """Fallo de transporte/ejecución traducido por el adaptador."""


class ExecutionBackend(Protocol):
    def submit(self, attempt: AttemptSpec) -> None:
        """Mismo attempt_id/contenido: no crear otra ejecución; distinto: error.

        Un envío de resultado incierto puede repetirse con el mismo identificador.
        Un reintento lógico usa siempre otro ID y una generación mayor.
        """
        ...

    def poll(self) -> tuple[AttemptOutcome, ...]:
        """Consulta no bloqueante; puede devolver notificaciones repetidas.

        Consultar no reconoce durabilidad. Retener hasta acknowledge o pérdida
        del backend; los intentos perdidos se recuperarán mediante plazos.
        """
        ...

    def acknowledge(self, attempt_id: str) -> None:
        """Olvidar notificación solo después de procesarla durablemente; idempotente."""
        ...

    def cancel(self, attempt_id: str) -> None:
        """Petición idempotente de mejor esfuerzo; aún puede llegar un resultado."""
        ...

    def workers(self) -> tuple[WorkerInfo, ...]:
        """Instantánea de identidades propias, no objetos del middleware."""
        ...


class MatchRepository(Protocol):
    def recovery_rows(self, tournament_id: str) -> list[dict]:
        """Partidas sin resolver y última generación, bajo el bloqueo del torneo."""
        ...

    def lose_current(self, match_id: str) -> str | None:
        """Cierra como perdido el intento anterior y libera su partida para reemplazo."""
        ...

    def bracket_rows(self, tournament_id: str) -> list[dict]: ...

    def summary(self, tournament_id: str) -> dict: ...

    def page(self, tournament_id: str, *, offset: int, limit: int) -> list[dict]: ...

    def next_generation(self, match_id: str) -> int: ...

    def finish_failure(self, outcome: AttemptOutcome, *, retry: bool) -> bool: ...

    def block_pending(self, tournament_id: str) -> None: ...

    def pending(self, tournament_id: str) -> tuple[MatchSpec, ...]: ...

    def start(self, match_id: str) -> None: ...

    def accepted(self, match_id: str) -> AttemptOutcome | None: ...

    def invalidate(self, match_id: str, attempt_id: str) -> bool: ...

    def cancel_pending(self, tournament_id: str) -> None: ...

    def get(self, match_id: str) -> MatchSpec: ...

    def current_attempt(self, match_id: str) -> AttemptSpec | None: ...

    def add_attempt(self, attempt: AttemptSpec) -> None:
        """ID nuevo y generación creciente; invalida el intento anterior."""
        ...

    def try_accept(self, outcome: AttemptOutcome) -> bool:
        """CAS transaccional: solo intento vigente y partida/torneo activos.

        Valida identidad y ganador contra MatchSpec. Solo completed o bot_failure.
        False para duplicados, resultados viejos o cancelados; nunca sobrescribe.
        Debe serializarse también frente a cancelación e invalidación del intento.
        """
        ...

    def set_state(self, match_id: str, state: MatchState) -> None: ...

    def set_attempt_state(self, attempt_id: str, state: AttemptState) -> None: ...


class TournamentRepository(Protocol):
    def recoverable_ids(self) -> tuple[str, ...]:
        """Torneos running con definición durable, en orden estable de creación."""
        ...

    def create(self, spec: TournamentSpec) -> None: ...

    def get(self, tournament_id: str) -> dict: ...

    def lock(self, tournament_id: str) -> dict: ...

    def state(self, tournament_id: str) -> TournamentState: ...

    def set_state(self, tournament_id: str, state: TournamentState) -> None: ...

    def advance(self, match: MatchSpec, outcome: AttemptOutcome) -> None:
        """Avance del cuadro/clasificación una sola vez tras aceptación válida."""
        ...


class EventRepository(Protocol):
    def next_sequence(self, tournament_id: str) -> int: ...

    def list(
        self, tournament_id: str, after_sequence: int = 0, *, limit: int = 1000,
    ) -> tuple[DomainEvent, ...]: ...

    def latest_sequence(self, tournament_id: str) -> int: ...

    def append(self, event: DomainEvent) -> None:
        """ID y secuencia por torneo únicos; visible externamente tras commit."""
        ...


class RegistryRepository(Protocol):
    def mark_available(self, artifact_hash: str) -> None: ...

    def find_competitor(self, competitor_id: str) -> RegisteredCompetitor | None: ...

    def get_competitor(self, competitor_id: str) -> RegisteredCompetitor: ...

    def add_artifact(
        self,
        artifact_hash: str,
        size_bytes: int,
        *,
        protocol: str = "nim-jsonl",
        protocol_version: int = 1,
        runtime: str = "python3.12",
    ) -> None: ...

    def add_competitor(self, competitor_id: str, name: str, artifact_hash: str) -> None: ...


class IdempotencyRepository(Protocol):
    def claim(self, scope: str, key: str, request_hash: str, resource_id: str) -> str: ...


class UnitOfWork(Protocol):
    registry: RegistryRepository
    idempotency: IdempotencyRepository
    matches: MatchRepository
    tournaments: TournamentRepository
    events: EventRepository

    def __enter__(self) -> Self: ...

    def commit(self) -> None:
        """Confirma conjuntamente aceptación, avance y eventos."""
        ...

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
        """Revierte cambios si hubo error o no se llamó a commit."""
        ...
