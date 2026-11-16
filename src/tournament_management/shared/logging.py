"""Salida JSON por stderr para aplicación y servidor HTTP."""

import json
import logging
import os
import sys
import time
from datetime import UTC, datetime


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "timestamp": datetime.now(UTC).isoformat(),
            "monotonic_ns": time.monotonic_ns(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        for key in ("tournament_id", "match_id", "attempt_id", "worker_id"):
            payload[key] = getattr(record, key, None)
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False)


def configure_logging(level: str) -> None:
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(JsonFormatter())
    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(level)
    for name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        logger = logging.getLogger(name)
        logger.handlers.clear()
        logger.propagate = True
        logger.setLevel(level)


def worker_event(event: str, *, attempt, worker_id: str, **details: object) -> None:
    """Escribe actividad de una tarea Ray en el log visible de su nodo.

    Ray redirige stderr de la tarea a archivos propios. El lanzador proporciona
    una ruta al log del nodo; un fallo de observabilidad nunca altera la partida.
    """
    path = os.environ.get("TM_WORKER_LOG_PATH")
    if not path:
        return
    record = {
        "timestamp": datetime.now(UTC).isoformat(),
        "level": "INFO",
        "logger": "tournament_management.worker",
        "message": event,
        "tournament_id": attempt.match.tournament_id,
        "match_id": attempt.match.match_id,
        "attempt_id": attempt.attempt_id,
        "worker_id": worker_id,
        **details,
    }
    try:
        descriptor = os.open(path, os.O_WRONLY | os.O_APPEND)
        try:
            os.write(descriptor, (json.dumps(record, ensure_ascii=False) + "\n").encode())
        finally:
            os.close(descriptor)
    except OSError:
        pass
