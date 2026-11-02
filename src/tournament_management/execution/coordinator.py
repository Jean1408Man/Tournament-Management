"""Coordinador de varios torneos e intentos simultáneos (5.1, E1.1–E1.4).

Un único hilo y una tabla de intentos en vuelo. Las partidas simultáneas las
dan los workers; este hilo es el único que emite, envía, recoge y confirma.
Cada vuelta: (1) confirma los resultados que haya, (2) vence plazos y (3)
mientras haya hueco, emite un intento para el siguiente torneo con partidas
listas. Ese turno rotatorio es una política de reparto, no el formato round
robin. Solo consulta PostgreSQL cuando hay hueco o llega un resultado.
"""

import logging
import math
import threading
import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass

from tournament_management.execution.attempt_runner import expire, send, settle
from tournament_management.execution.backend import ExecutionBackend, ExecutionError
from tournament_management.execution.cancellation import request_stop
from tournament_management.execution.policy import ExecutionPolicy
from tournament_management.execution.recovery import StartupRecovery
from tournament_management.shared.messages import AttemptSpec, WorkerStatus
from tournament_management.shared.unit_of_work import UnitOfWork
from tournament_management.tournaments.queries import TournamentQueries
from tournament_management.tournaments.service import (
    PersistentTournamentService,
)

logger = logging.getLogger(__name__)
TICK_SECONDS = 0.02


@dataclass(frozen=True)
class _InFlight:
    tournament_id: str
    attempt: AttemptSpec
    retry: bool
    deadline: float


class TournamentCoordinator:
    def __init__(
        self,
        work_factory: Callable[[], UnitOfWork],
        backend: ExecutionBackend,
        results: PersistentTournamentService,
        policy: ExecutionPolicy,
    ) -> None:
        self.work_factory = work_factory
        self.backend = backend
        self.results = results
        self.policy = policy
        self.queries = TournamentQueries(work_factory)
        self.stopping = threading.Event()
        # Protege lo que tocan también los hilos HTTP (enqueue, cancel, close).
        self._condition = threading.Condition(threading.RLock())
        self._turns: deque[str] = deque()
        # Torneo sin partidas emitibles: no se consulta hasta esa hora o hasta
        # que llegue un resultado suyo.
        self._idle_until: dict[str, float] = {}
        # Espera entre reintentos por partida, sin dormir el hilo.
        self._not_before: dict[str, float] = {}
        self._in_flight: dict[str, _InFlight] = {}
        self._starved_since: float | None = None
        self._failed = False
        self._thread = threading.Thread(target=self._loop, name="tm-coordinator", daemon=True)

    @property
    def healthy(self) -> bool:
        return self._thread.is_alive() and not self._failed and not self.stopping.is_set()

    def start(self) -> None:
        identifiers = StartupRecovery(
            self.work_factory, max_retries=self.policy.max_retries
        ).recover()
        for tournament_id in identifiers:
            self.enqueue(tournament_id)
        logger.info("Recuperación completada: %s torneos pendientes", len(identifiers))
        self._thread.start()

    def enqueue(self, tournament_id: str) -> None:
        with self._condition:
            if tournament_id not in self._turns:
                self._turns.append(tournament_id)
            self._idle_until.pop(tournament_id, None)
            self._condition.notify()

    def cancel(self, tournament_id: str) -> None:
        # El llamador confirma primero la cancelación SQL. El bloqueo cubre el
        # intervalo entre emitir un intento y anotarlo en la tabla.
        with self._condition:
            self._stop(tournament_id)
            self._idle_until.pop(tournament_id, None)

    def close(self) -> None:
        self.stopping.set()
        with self._condition:
            for attempt_id in self._in_flight:
                request_stop(self.backend, attempt_id)
            self._condition.notify_all()
        if self._thread.ident is not None:
            self._thread.join()

    def _loop(self) -> None:
        try:
            while not self.stopping.is_set():
                self._collect()
                self._expire()
                self._dispatch()
                with self._condition:
                    busy = self._turns or self._in_flight
                    if not self.stopping.is_set():
                        self._condition.wait(TICK_SECONDS if busy else None)
        except Exception:
            # Sin commit no hay acuse. Ante un fallo inesperado se deja de
            # admitir trabajo y /ready da 503; no se inventa una resolución.
            self._failed = True
            logger.exception("Coordinador detenido por fallo de ejecución o persistencia")

    def _collect(self) -> None:
        for outcome in self.backend.poll():
            with self._condition:
                entry = self._in_flight.pop(outcome.attempt_id, None)
            if entry is None:
                # Un intento vencido o ya sustituido puede entregar tarde. La
                # aceptación transaccional vuelve a comprobar generación y vigencia.
                self.results.accept(outcome)
            elif not settle(self.results, outcome, retry=entry.retry):
                self._after_failure(entry)
            else:
                self._idle_until.pop(entry.tournament_id, None)
            self.backend.acknowledge(outcome.attempt_id)

    def _expire(self) -> None:
        now = time.monotonic()
        for attempt_id, entry in list(self._in_flight.items()):
            if now >= entry.deadline:
                with self._condition:
                    del self._in_flight[attempt_id]
                expire(self.backend, self.results, entry.attempt, retry=entry.retry)
                self._after_failure(entry)

    def _after_failure(self, entry: _InFlight) -> None:
        """Sin resultado aceptado: esperar antes de reintentar; si el torneo se
        bloqueó o canceló, parar el resto de sus intentos."""
        self._not_before[entry.attempt.match.match_id] = (
            time.monotonic() + self.policy.retry_delay_ms / 1000
        )
        self._idle_until.pop(entry.tournament_id, None)
        if self.queries.state(entry.tournament_id) != "running":
            with self._condition:
                self._stop(entry.tournament_id)

    def _dispatch(self) -> None:
        free = self._free_slots()
        misses = 0
        while free > 0 and not self.stopping.is_set():
            with self._condition:
                if misses >= len(self._turns):
                    return
                tournament_id = self._turns[0]
                self._turns.rotate(-1)
            if self._idle_until.get(tournament_id, 0) > time.monotonic():
                misses += 1
            elif self._issue_next(tournament_id):
                free -= 1
                misses = 0
            else:
                misses += 1

    def _issue_next(self, tournament_id: str) -> bool:
        """Emite y envía un intento de la primera partida lista; False si no hay."""
        now = time.monotonic()
        pending = self.results.pending(tournament_id)
        ready = [m for m in pending if self._not_before.get(m.match_id, 0) <= now]
        if not ready:
            if not pending and self.queries.state(tournament_id) != "running":
                self._drop(tournament_id)
            else:
                waits = [self._not_before[m.match_id] for m in pending]
                self._idle_until[tournament_id] = min(waits, default=math.inf)
            return False
        match = ready[0]
        with self.work_factory() as work:
            generation = work.matches.next_generation(match.match_id)
        attempt = self.policy.attempt(match, generation)
        entry = _InFlight(
            tournament_id,
            attempt,
            retry=generation <= self.policy.max_retries,
            deadline=time.monotonic() + self.policy.attempt_timeout_ms / 1000,
        )
        with self._condition:
            if self.stopping.is_set():
                return False
            try:
                self.results.issue(attempt)
            except ValueError:
                # Puede haber ganado la cancelación mientras se preparaba.
                if self.queries.state(tournament_id) != "running":
                    self._drop(tournament_id)
                    return False
                raise
            self._in_flight[attempt.attempt_id] = entry
        self._not_before.pop(match.match_id, None)
        if not send(self.backend, self.results, attempt, retry=entry.retry):
            with self._condition:
                del self._in_flight[attempt.attempt_id]
            self._after_failure(entry)
        return True

    def _free_slots(self) -> int:
        """Huecos para emitir ahora: min(capacidad libre, TM_MAX_RUNNING_ATTEMPTS).

        Bloquea con no_worker_capacity solo si no hay hueco y ningún intento
        propio en vuelo durante TM_CAPACITY_WAIT_MS: esperar turno no cuenta.
        """
        try:
            workers = self.backend.workers()
        except ExecutionError:
            workers = ()
        capacity = sum(
            max(0, w.capacity - w.running_attempts)
            for w in workers
            if w.status is WorkerStatus.AVAILABLE
        )
        free = min(capacity, self.policy.max_running_attempts - len(self._in_flight))
        if free > 0 or self._in_flight or not self._turns:
            if self._starved_since is not None:
                logger.info("Capacidad de workers recuperada")
            self._starved_since = None
            return free
        now = time.monotonic()
        if self._starved_since is None:
            logger.info("Sin capacidad de workers: %s torneos esperan", len(self._turns))
            self._starved_since = now
        elif now - self._starved_since >= self.policy.capacity_wait_ms / 1000:
            for tournament_id in list(self._turns):
                logger.warning("Torneo %s bloqueado: sin capacidad de workers", tournament_id)
                self.results.block(tournament_id, "no_worker_capacity")
                self._drop(tournament_id)
            self._starved_since = None
        return 0

    def _drop(self, tournament_id: str) -> None:
        with self._condition:
            if tournament_id in self._turns:
                self._turns.remove(tournament_id)
            self._idle_until.pop(tournament_id, None)

    def _stop(self, tournament_id: str) -> None:
        for attempt_id, entry in self._in_flight.items():
            if entry.tournament_id == tournament_id:
                request_stop(self.backend, attempt_id)
