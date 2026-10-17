"""Puertos sin referencias Ray; no implementan ejecución ni persistencia."""

from typing import Protocol, Self

from tournament_management.domain.messages import (
    AttemptOutcome,
    AttemptSpec,
    DomainEvent,
    MatchSpec,
    WorkerInfo,
)
from tournament_management.domain.states import AttemptState, MatchState, TournamentState


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
    def state(self, tournament_id: str) -> TournamentState: ...

    def set_state(self, tournament_id: str, state: TournamentState) -> None: ...

    def advance(self, match: MatchSpec, outcome: AttemptOutcome) -> None:
        """Avance del cuadro/clasificación una sola vez tras aceptación válida."""
        ...


class EventRepository(Protocol):
    def append(self, event: DomainEvent) -> None:
        """ID y secuencia por torneo únicos; visible externamente tras commit."""
        ...


class UnitOfWork(Protocol):
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
