"""Vida de un intento: emitir, enviar, esperar y confirmar antes del acuse."""

import threading
import time
from collections.abc import Callable

from tournament_management.execution.backend import ExecutionBackend, ExecutionError
from tournament_management.execution.cancellation import request_stop
from tournament_management.execution.policy import ExecutionPolicy
from tournament_management.shared.messages import AttemptOutcome, AttemptSpec, OutcomeKind
from tournament_management.tournaments.service import (
    PersistentTournamentService,
)


class AttemptRunner:
    def __init__(
        self,
        backend: ExecutionBackend,
        results: PersistentTournamentService,
        policy: ExecutionPolicy,
        stopping: threading.Event,
    ) -> None:
        self.backend = backend
        self.results = results
        self.policy = policy
        self.stopping = stopping

    def run(self, attempt: AttemptSpec, *, retry: bool, cancelled: Callable[[], bool]) -> None:
        """El coordinador ya emitió el intento bajo su bloqueo de cancelación."""
        if self.stopping.is_set() or cancelled():
            return
        try:
            self.backend.submit(attempt)
        except ExecutionError:
            self.results.finish_failure(self._failure(attempt, "submission_failed"), retry=retry)
            return
        deadline = time.monotonic() + self.policy.attempt_timeout_ms / 1000
        while not self.stopping.is_set():
            if cancelled():
                request_stop(self.backend, attempt.attempt_id)
                return
            for outcome in self.backend.poll():
                if outcome.attempt_id != attempt.attempt_id:
                    # Un intento expirado puede entregar tarde. La aceptación
                    # transaccional vuelve a comprobar su generación y vigencia.
                    self.results.accept(outcome)
                    self.backend.acknowledge(outcome.attempt_id)
                    continue
                if outcome.kind in {OutcomeKind.COMPLETED, OutcomeKind.BOT_FAILURE}:
                    self.results.accept(outcome)
                else:
                    self.results.finish_failure(
                        outcome,
                        retry=retry and outcome.kind == OutcomeKind.INFRASTRUCTURE_FAILURE,
                    )
                self.backend.acknowledge(outcome.attempt_id)
                return
            if time.monotonic() >= deadline:
                # Invalidación durable antes de cancelar físicamente o reemplazar.
                self.results.finish_failure(self._failure(attempt, "attempt_timeout"), retry=retry)
                request_stop(self.backend, attempt.attempt_id)
                return
            self.stopping.wait(0.02)

    def _failure(self, attempt: AttemptSpec, reason: str) -> AttemptOutcome:
        return AttemptOutcome(
            match_id=attempt.match.match_id,
            attempt_id=attempt.attempt_id,
            generation=attempt.generation,
            # Un envío rechazado no llegó a tener worker.
            worker_id=self.backend.worker_of(attempt.attempt_id) or "unassigned",
            kind=OutcomeKind.INFRASTRUCTURE_FAILURE,
            winner_id="",
            reason=reason,
        )
