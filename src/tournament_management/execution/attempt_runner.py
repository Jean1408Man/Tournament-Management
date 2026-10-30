"""Vida de un intento paso a paso; el coordinador decide cuándo dar cada paso (5.1).

Órdenes que se conservan desde 3.x: el intento se emite (durable) antes de
enviarse, un resultado se confirma antes del acuse y un plazo vencido se
registra antes de pedir la parada física.
"""

from tournament_management.execution.backend import ExecutionBackend, ExecutionError
from tournament_management.execution.cancellation import request_stop
from tournament_management.shared.messages import AttemptOutcome, AttemptSpec, OutcomeKind
from tournament_management.tournaments.service import (
    PersistentTournamentService,
)


def send(
    backend: ExecutionBackend,
    results: PersistentTournamentService,
    attempt: AttemptSpec,
    *,
    retry: bool,
) -> bool:
    """Envía un intento ya emitido; False si el envío falló (queda registrado)."""
    try:
        backend.submit(attempt)
    except ExecutionError:
        results.finish_failure(failure(backend, attempt, "submission_failed"), retry=retry)
        return False
    results.dispatched(attempt, backend.worker_of(attempt.attempt_id) or "unassigned")
    return True


def settle(results: PersistentTournamentService, outcome: AttemptOutcome, *, retry: bool) -> bool:
    """Confirma lo que devolvió un intento; True si se aceptó un resultado deportivo."""
    if outcome.kind in {OutcomeKind.COMPLETED, OutcomeKind.BOT_FAILURE}:
        return results.accept(outcome)
    results.finish_failure(
        outcome, retry=retry and outcome.kind == OutcomeKind.INFRASTRUCTURE_FAILURE
    )
    return False


def expire(
    backend: ExecutionBackend,
    results: PersistentTournamentService,
    attempt: AttemptSpec,
    *,
    retry: bool,
) -> None:
    # Invalidación durable antes de cancelar físicamente o reemplazar.
    results.finish_failure(failure(backend, attempt, "attempt_timeout"), retry=retry)
    request_stop(backend, attempt.attempt_id)


def failure(backend: ExecutionBackend, attempt: AttemptSpec, reason: str) -> AttemptOutcome:
    return AttemptOutcome(
        match_id=attempt.match.match_id,
        attempt_id=attempt.attempt_id,
        generation=attempt.generation,
        # Un envío rechazado no llegó a tener worker.
        worker_id=backend.worker_of(attempt.attempt_id) or "unassigned",
        kind=OutcomeKind.INFRASTRUCTURE_FAILURE,
        winner_id="",
        reason=reason,
    )
