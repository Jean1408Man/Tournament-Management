"""Aceptación local de resultados: una sola generación vigente por partida."""

from __future__ import annotations

import threading
from dataclasses import dataclass

from tournament_management.shared.messages import (
    AttemptOutcome,
    AttemptSpec,
    MatchSpec,
    OutcomeKind,
)


@dataclass
class _Record:
    match: MatchSpec
    current: AttemptSpec | None = None
    accepted: AttemptOutcome | None = None
    max_generation: int = 0
    cancelled: bool = False


class ResultService:
    """CAS en memoria; el bloqueo serializa resultado, reintento y cancelación."""

    def __init__(self) -> None:
        self._records: dict[str, _Record] = {}
        self._lock = threading.RLock()

    def issue(self, attempt: AttemptSpec) -> None:
        with self._lock:
            record = self._records.setdefault(attempt.match.match_id, _Record(attempt.match))
            if record.match != attempt.match or record.cancelled or record.accepted is not None:
                raise ValueError("La partida no acepta más intentos")
            if attempt.generation <= record.max_generation:
                raise ValueError("La generación debe aumentar")
            record.current = attempt
            record.max_generation = attempt.generation

    def invalidate(self, match_id: str, attempt_id: str) -> None:
        with self._lock:
            record = self._records[match_id]
            if record.current is not None and record.current.attempt_id == attempt_id:
                record.current = None

    def cancel(self, match_id: str) -> None:
        with self._lock:
            record = self._records[match_id]
            record.cancelled = True
            record.current = None

    def is_cancelled(self, match_id: str) -> bool:
        with self._lock:
            return self._records[match_id].cancelled

    def accept(self, outcome: AttemptOutcome) -> bool:
        with self._lock:
            record = self._records.get(outcome.match_id)
            if record is None or record.cancelled or record.accepted is not None:
                return False
            current = record.current
            if current is None or outcome.kind not in (
                OutcomeKind.COMPLETED, OutcomeKind.BOT_FAILURE
            ):
                return False
            try:
                outcome.validate_for(current)
            except ValueError:
                return False
            record.accepted = outcome
            record.current = None
            return True

    def accepted(self, match_id: str) -> AttemptOutcome | None:
        with self._lock:
            return self._records[match_id].accepted
