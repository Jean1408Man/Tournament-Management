"""Vida de un intento paso a paso; el coordinador decide cuándo dar cada paso.

Órdenes que se conservan desde 3.x: el intento se emite (durable) antes de
enviarse, un resultado se confirma antes del acuse y un plazo vencido se
registra antes de pedir la parada física.
"""

import logging

from tournament_management.execution.backend import ExecutionBackend, ExecutionError
from tournament_management.execution.cancellation import request_stop
from tournament_management.shared.messages import AttemptOutcome, AttemptSpec, OutcomeKind
from tournament_management.tournaments.service import (
    PersistentTournamentService,
)

logger = logging.getLogger(__name__)


def _context(attempt: AttemptSpec, worker_id: str | None = None) -> dict:
    return {
        "tournament_id": attempt.match.tournament_id,
        "match_id": attempt.match.match_id,
        "attempt_id": attempt.attempt_id,
        "worker_id": worker_id,
    }


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
        logger.warning("Envío rechazado por el backend; reintento=%s", retry,
                       extra=_context(attempt))
        return False
    worker_id = backend.worker_of(attempt.attempt_id) or "unassigned"
    results.dispatched(attempt, worker_id)
    logger.info("Intento enviado al worker; generación=%s", attempt.generation,
                extra=_context(attempt, worker_id))
    return True


def settle(results: PersistentTournamentService, outcome: AttemptOutcome, *, retry: bool) -> bool:
    """Confirma lo que devolvió un intento; True si se aceptó un resultado deportivo."""
    if outcome.kind in {OutcomeKind.COMPLETED, OutcomeKind.BOT_FAILURE}:
        accepted = results.accept(outcome)
        logger.info("Resultado %s; tipo=%s motivo=%s", "aceptado" if accepted else "descartado",
                    outcome.kind.value, outcome.reason,
                    extra={"match_id": outcome.match_id, "attempt_id": outcome.attempt_id,
                           "worker_id": outcome.worker_id})
        return accepted
    results.finish_failure(
        outcome, retry=retry and outcome.kind == OutcomeKind.INFRASTRUCTURE_FAILURE
    )
    logger.warning("Intento fallido; tipo=%s motivo=%s reintento=%s", outcome.kind.value,
                   outcome.reason, retry,
                   extra={"match_id": outcome.match_id, "attempt_id": outcome.attempt_id,
                          "worker_id": outcome.worker_id})
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
    logger.warning("Plazo del intento vencido; reintento=%s", retry,
                   extra=_context(attempt, backend.worker_of(attempt.attempt_id)))
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
