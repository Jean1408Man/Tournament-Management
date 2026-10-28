"""Casos de uso durables de 3.1, independientes de PostgreSQL y del backend.

Cada operación abre una UoW corta. Nunca se ejecutan bots ni se llama al
middleware dentro de la transacción. El acuse de una notificación corresponde
al llamador, después de que accept() haya retornado (y por tanto confirmado).
"""

import hashlib
import json
from collections.abc import Callable

from tournament_management.events.durable import append_event
from tournament_management.shared.errors import ConflictError
from tournament_management.shared.messages import (
    AttemptOutcome,
    AttemptSpec,
    DomainEvent,
    MatchSpec,
)
from tournament_management.shared.states import AttemptState, TournamentState
from tournament_management.shared.unit_of_work import UnitOfWork
from tournament_management.tournaments.model import TournamentSpec


class PersistentTournamentService:
    def __init__(
        self,
        work_factory: Callable[[], UnitOfWork],
        publish: Callable[[DomainEvent], None] | None = None,
    ) -> None:
        self.work_factory = work_factory
        self.publish = publish

    def create(self, spec: TournamentSpec, *, idempotency_key: str) -> str:
        digest = hashlib.sha256(spec.to_json().encode()).hexdigest()
        with self.work_factory() as work:
            identifier = work.idempotency.claim(
                "tournament.create",
                idempotency_key,
                digest,
                spec.tournament_id,
            )
            event = None
            try:
                row = work.tournaments.get(identifier)
            except KeyError:
                work.tournaments.create(spec)
                event = self._event(work, spec.tournament_id, "tournament.created")
            else:
                if row["definition"] != json.loads(spec.to_json()):
                    raise ConflictError("El identificador de torneo ya tiene otra definición")
            work.commit()
        self._publish(event)
        return identifier

    def pending(self, tournament_id: str) -> tuple[MatchSpec, ...]:
        with self.work_factory() as work:
            return work.matches.pending(tournament_id)

    def issue(self, attempt: AttemptSpec) -> None:
        with self.work_factory() as work:
            work.matches.add_attempt(attempt)
            work.matches.set_attempt_state(attempt.attempt_id, AttemptState.RUNNING)
            # Un reemplazo directo puede partir de una partida ya running.
            work.matches.start(attempt.match.match_id)
            event = self._event(
                work,
                attempt.match.tournament_id,
                "attempt.issued",
                attempt.match.match_id,
                attempt.attempt_id,
            )
            work.commit()
        self._publish(event)

    def accept(self, outcome: AttemptOutcome) -> bool:
        with self.work_factory() as work:
            if not work.matches.try_accept(outcome):
                return False
            match = work.matches.get(outcome.match_id)
            work.tournaments.advance(match, outcome)
            event = self._event(
                work,
                match.tournament_id,
                "result.accepted",
                outcome.match_id,
                outcome.attempt_id,
            )
            work.commit()
        # El historial durable es la fuente para reconexión/republicación. Esto
        # es solo una señal rápida: un fallo aquí no deshace el commit ni ofrece
        # entrega exactamente una vez. El consumidor deduplica por event_id.
        self._publish(event)
        return True

    def accepted(self, match_id: str) -> AttemptOutcome | None:
        with self.work_factory() as work:
            return work.matches.accepted(match_id)

    def invalidate(self, match_id: str, attempt_id: str) -> None:
        event = None
        with self.work_factory() as work:
            if work.matches.invalidate(match_id, attempt_id):
                match = work.matches.get(match_id)
                event = self._event(
                    work,
                    match.tournament_id,
                    "attempt.invalidated",
                    match_id,
                    attempt_id,
                )
            work.commit()
        self._publish(event)

    def cancel_tournament(self, tournament_id: str) -> None:
        """Cierra la aceptación; parar físicamente ejecuciones es responsabilidad externa."""
        with self.work_factory() as work:
            row = work.tournaments.lock(tournament_id)
            state = TournamentState(row["state"])
            if state in {TournamentState.CANCELLED, TournamentState.COMPLETED}:
                return
            work.tournaments.set_state(tournament_id, TournamentState.CANCELLED)
            work.matches.cancel_pending(tournament_id)
            event = self._event(work, tournament_id, "tournament.cancelled")
            work.commit()
        self._publish(event)

    def block(self, tournament_id: str, reason: str) -> bool:
        """Bloquea un torneo en curso con motivo; estado, partidas y evento juntos."""
        with self.work_factory() as work:
            row = work.tournaments.lock(tournament_id)
            if row["state"] != TournamentState.RUNNING.value:
                return False
            work.tournaments.set_state(tournament_id, TournamentState.BLOCKED)
            work.matches.block_pending(tournament_id)
            event = append_event(work, tournament_id, "tournament.blocked", detail=reason)
            work.commit()
        self._publish(event)
        return True

    def finish_failure(self, outcome: AttemptOutcome, *, retry: bool) -> bool:
        """Persistir diagnóstico antes del acuse; bloquear si no queda reintento."""
        with self.work_factory() as work:
            if not work.matches.finish_failure(outcome, retry=retry):
                return False
            match = work.matches.get(outcome.match_id)
            self._event(
                work,
                match.tournament_id,
                "attempt.failed",
                outcome.match_id,
                outcome.attempt_id,
            )
            if not retry:
                work.tournaments.set_state(match.tournament_id, TournamentState.BLOCKED)
                work.matches.block_pending(match.tournament_id)
                self._event(work, match.tournament_id, "tournament.blocked")
            work.commit()
        return True

    def _event(
        self,
        work: UnitOfWork,
        tournament_id: str,
        event_type: str,
        match_id: str = "",
        attempt_id: str = "",
    ) -> DomainEvent:
        return append_event(work, tournament_id, event_type, match_id, attempt_id)

    def _publish(self, event: DomainEvent | None) -> None:
        if self.publish is not None and event is not None:
            self.publish(event)
