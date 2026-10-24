"""Detención física de mejor esfuerzo; la cancelación durable se confirma antes."""

import logging

from tournament_management.application.ports import ExecutionBackend

logger = logging.getLogger(__name__)


def request_stop(backend: ExecutionBackend, attempt_id: str) -> None:
    try:
        backend.cancel(attempt_id)
    except Exception:
        # Un fallo del middleware no revoca la cancelación ni su protección
        # contra resultados tardíos. También se conserva el timeout confirmado.
        logger.exception("No se pudo solicitar la detención del intento %s", attempt_id)
