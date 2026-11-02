"""Backend local de intentos conforme al puerto ExecutionBackend."""

from __future__ import annotations

import threading
from collections.abc import Callable
from concurrent.futures import Future, ThreadPoolExecutor

from tournament_management.execution.backend import ExecutionError
from tournament_management.shared.messages import (
    AttemptOutcome,
    AttemptSpec,
    OutcomeKind,
    WorkerInfo,
    WorkerStatus,
)


class LocalExecution:
    def __init__(
        self,
        execute: Callable[[AttemptSpec, threading.Event], AttemptOutcome],
        *,
        capacity: int = 1,
    ) -> None:
        if capacity < 1:
            raise ValueError("La capacidad local debe ser positiva")
        self._execute = execute
        self._capacity = capacity
        self._pool = ThreadPoolExecutor(max_workers=capacity, thread_name_prefix="tm-local")
        self._attempts: dict[str, tuple[AttemptSpec, Future, threading.Event]] = {}
        self._acknowledged: set[str] = set()
        self._lock = threading.RLock()
        self._closed = False

    def submit(self, attempt: AttemptSpec) -> None:
        with self._lock:
            if self._closed:
                raise ExecutionError("Backend local cerrado")
            previous = self._attempts.get(attempt.attempt_id)
            if previous is not None:
                if previous[0] != attempt:
                    raise ExecutionError("ID de intento reutilizado con otro contenido")
                return
            cancel = threading.Event()
            future = self._pool.submit(self._execute, attempt, cancel)
            self._attempts[attempt.attempt_id] = (attempt, future, cancel)

    def poll(self) -> tuple[AttemptOutcome, ...]:
        with self._lock:
            outcomes = []
            for attempt_id, (attempt, future, _) in self._attempts.items():
                if attempt_id in self._acknowledged or not future.done():
                    continue
                if future.cancelled():
                    kind, reason = OutcomeKind.INFRASTRUCTURE_FAILURE, "cancelled"
                    outcome = _failure(attempt, kind, reason)
                else:
                    try:
                        outcome = future.result()
                        outcome.validate_for(attempt)
                    except Exception as exc:
                        outcome = _failure(attempt, OutcomeKind.INFRASTRUCTURE_FAILURE, str(exc))
                outcomes.append(outcome)
            return tuple(outcomes)

    def acknowledge(self, attempt_id: str) -> None:
        with self._lock:
            if attempt_id not in self._attempts:
                raise KeyError(attempt_id)
            self._acknowledged.add(attempt_id)

    def cancel(self, attempt_id: str) -> None:
        with self._lock:
            item = self._attempts.get(attempt_id)
            if item is not None:
                item[2].set()
                item[1].cancel()

    def workers(self) -> tuple[WorkerInfo, ...]:
        with self._lock:
            # Un intento en cola ocupa hueco igual que uno en marcha: no sobreenviar.
            busy = sum(not future.done() for _, future, _ in self._attempts.values())
            return (WorkerInfo(
                worker_id="local", status=WorkerStatus.AVAILABLE,
                capacity=self._capacity, running_attempts=min(busy, self._capacity),
            ),)

    def worker_of(self, attempt_id: str) -> str:
        with self._lock:
            return "local" if attempt_id in self._attempts else ""

    def close(self) -> None:
        with self._lock:
            self._closed = True
            for _, future, cancel in self._attempts.values():
                cancel.set()
                future.cancel()
        self._pool.shutdown(wait=True, cancel_futures=True)

    def __enter__(self) -> LocalExecution:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()


def _failure(attempt: AttemptSpec, kind: OutcomeKind, reason: str) -> AttemptOutcome:
    return AttemptOutcome(
        match_id=attempt.match.match_id, attempt_id=attempt.attempt_id,
        generation=attempt.generation, worker_id="local", kind=kind,
        winner_id="", reason=reason or "execution failed",
    )
