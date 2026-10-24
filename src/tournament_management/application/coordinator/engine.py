"""Cola local reconstruida desde PostgreSQL antes de iniciar el hilo ejecutor."""

import logging
import threading
from collections import deque
from collections.abc import Callable

from tournament_management.application.coordinator.attempt_runner import AttemptRunner
from tournament_management.application.coordinator.cancellation import request_stop
from tournament_management.application.coordinator.policy import ExecutionPolicy
from tournament_management.application.coordinator.queries import TournamentQueries
from tournament_management.application.coordinator.recovery import StartupRecovery
from tournament_management.application.persistent_tournament_service import (
    PersistentTournamentService,
)
from tournament_management.application.ports import ExecutionBackend, UnitOfWork

logger = logging.getLogger(__name__)


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
        self._condition = threading.Condition(threading.RLock())
        self._queue: deque[str] = deque()
        self._scheduled: set[str] = set()
        self._current: tuple[str, str] | None = None
        self._failed = False
        self._thread = threading.Thread(target=self._loop, name="tm-coordinator", daemon=True)
        self._runner = AttemptRunner(backend, results, policy, self.stopping)

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
            if tournament_id not in self._scheduled:
                self._scheduled.add(tournament_id)
                self._queue.append(tournament_id)
                self._condition.notify()

    def cancel(self, tournament_id: str) -> None:
        # El llamador confirma primero la cancelación SQL. Este bloqueo cubre
        # el intervalo entre emitir y publicar el ID activo al hilo HTTP.
        with self._condition:
            if self._current and self._current[0] == tournament_id:
                request_stop(self.backend, self._current[1])

    def close(self) -> None:
        self.stopping.set()
        with self._condition:
            if self._current:
                request_stop(self.backend, self._current[1])
            self._condition.notify_all()
        if self._thread.ident is not None:
            self._thread.join()

    def _loop(self) -> None:
        while not self.stopping.is_set():
            with self._condition:
                self._condition.wait_for(lambda: self._queue or self.stopping.is_set())
                if self.stopping.is_set():
                    return
                tournament_id = self._queue.popleft()
            try:
                self._run(tournament_id)
            except Exception:
                # Sin commit no hay acuse. Ante un fallo inesperado se deja de
                # admitir trabajo y /ready da 503; no se inventa una resolución.
                self._failed = True
                logger.exception("Coordinador detenido por fallo de ejecución o persistencia")
                return
            finally:
                with self._condition:
                    self._scheduled.discard(tournament_id)
                    self._current = None

    def _run(self, tournament_id: str) -> None:
        while not self.stopping.is_set() and self.queries.state(tournament_id) == "running":
            pending = self.results.pending(tournament_id)
            if not pending:
                return
            match = pending[0]
            with self.work_factory() as work:
                generation = work.matches.next_generation(match.match_id)
            attempt = self.policy.attempt(match, generation)
            with self._condition:
                if self.stopping.is_set():
                    return
                try:
                    self.results.issue(attempt)
                except ValueError:
                    # Puede haber ganado la cancelación mientras se preparaba.
                    if self.queries.state(tournament_id) != "running":
                        return
                    raise
                self._current = (tournament_id, attempt.attempt_id)
            self._runner.run(
                attempt,
                retry=generation <= self.policy.max_retries,
                cancelled=lambda: self.queries.state(tournament_id) != "running",
            )
            if self.queries.state(tournament_id) == "running":
                with self.work_factory() as work:
                    accepted = work.matches.accepted(match.match_id)
                if accepted is None:
                    self.stopping.wait(self.policy.retry_delay_ms / 1000)
