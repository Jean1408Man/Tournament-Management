"""Scheduler local secuencial que solo conoce el puerto de ejecución."""

from __future__ import annotations

import threading
import time
from dataclasses import replace

from tournament_management.application.ports import ExecutionBackend, ExecutionError
from tournament_management.application.result_service import ResultService
from tournament_management.domain.messages import AttemptOutcome, AttemptSpec, OutcomeKind


class Scheduler:
    def __init__(
        self,
        backend: ExecutionBackend,
        results: ResultService,
        *,
        max_retries: int = 3,
        attempt_timeout_ms: int = 90_000,
    ) -> None:
        if max_retries < 0 or attempt_timeout_ms <= 0:
            raise ValueError("Presupuesto de intentos inválido")
        self.backend = backend
        self.results = results
        self.max_retries = max_retries
        self.attempt_timeout_ms = attempt_timeout_ms
        self._active: dict[str, str] = {}
        self._lock = threading.RLock()

    def cancel(self, match_id: str) -> None:
        with self._lock:
            self.results.cancel(match_id)
            attempt_id = self._active.get(match_id)
            if attempt_id is not None:
                self.backend.cancel(attempt_id)

    def run_match(self, first: AttemptSpec) -> AttemptOutcome:
        """Reintenta solo infraestructura; el resultado deportivo se acepta una vez."""
        match_id = first.match.match_id
        attempt = first
        try:
            for retry in range(self.max_retries + 1):
                if retry:
                    attempt = replace(
                        first, attempt_id=f"{first.attempt_id}-retry-{retry}",
                        generation=first.generation + retry,
                    )
                with self._lock:
                    if retry and self.results.is_cancelled(match_id):
                        return _failure(attempt, "cancelled")
                    self.results.issue(attempt)
                    self._active[match_id] = attempt.attempt_id
                    try:
                        self.backend.submit(attempt)
                    except ExecutionError as exc:
                        self.results.invalidate(match_id, attempt.attempt_id)
                        if retry == self.max_retries:
                            return _failure(attempt, str(exc))
                        continue
                deadline = time.monotonic() + self.attempt_timeout_ms / 1000
                while True:
                    if self.results.is_cancelled(match_id):
                        return _failure(attempt, "cancelled")
                    current_outcome = None
                    for outcome in self.backend.poll():
                        accepted = self.results.accept(outcome)
                        self.backend.acknowledge(outcome.attempt_id)
                        if outcome.attempt_id == attempt.attempt_id:
                            if current_outcome is None or accepted:
                                current_outcome = (outcome, accepted)
                    if current_outcome is not None:
                        outcome, accepted = current_outcome
                        if accepted:
                            return outcome
                        if outcome.kind == OutcomeKind.NO_WINNER:
                            self.results.invalidate(match_id, attempt.attempt_id)
                            return outcome
                        if outcome.kind == OutcomeKind.INFRASTRUCTURE_FAILURE:
                            break
                    if time.monotonic() >= deadline:
                        outcome = _failure(attempt, "attempt timeout")
                        break
                    time.sleep(0.01)
                # Invalidación precede a la cancelación y al envío del reintento.
                self.results.invalidate(match_id, attempt.attempt_id)
                self.backend.cancel(attempt.attempt_id)
                if retry == self.max_retries:
                    return outcome
            raise RuntimeError("Presupuesto de reintentos agotado")
        finally:
            with self._lock:
                self._active.pop(match_id, None)


def _failure(attempt: AttemptSpec, reason: str) -> AttemptOutcome:
    return AttemptOutcome(
        match_id=attempt.match.match_id, attempt_id=attempt.attempt_id,
        generation=attempt.generation, worker_id="local",
        kind=OutcomeKind.INFRASTRUCTURE_FAILURE, winner_id="", reason=reason,
    )
