"""Backend Ray del puerto ExecutionBackend (etapa 4.1).

El adaptador elige el worker de cada intento (afinidad estricta) y Ray solo
transporta la tarea y avisa de caídas. Ray no reintenta: un intento perdido
vuelve como infrastructure_failure y la plataforma emite otra generación.
Intentos y resultados viajan como JSON; los IDs de nodo Ray no salen de aquí.
"""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from itertools import count

from tournament_management.execution.backend import ExecutionError
from tournament_management.execution.membership import WorkerMembership
from tournament_management.shared.messages import (
    AttemptOutcome,
    AttemptSpec,
    OutcomeKind,
    WorkerInfo,
    WorkerStatus,
)

logger = logging.getLogger(__name__)


def play_attempt(attempt_json: str, worker_id: str) -> str:
    """Se ejecuta en el worker; recibe su identidad pública del adaptador.

    La dirección del coordinador y el token salen del entorno del worker (D2.4),
    nunca de la tarea: el secreto no viaja por Ray.
    """
    import os

    from tournament_management.competitors.remote_store import RemoteArtifactStore
    from tournament_management.config import Settings
    from tournament_management.execution.backends.registered import run_registered_match

    attempt = AttemptSpec.from_json(attempt_json)
    try:
        settings = Settings.load({}, os.environ)
        store = RemoteArtifactStore(
            settings.artifact_url, settings.internal_token,
            max_bytes=settings.artifact_max_bytes,
            timeout_ms=settings.artifact_download_timeout_ms,
        )
    except ValueError:
        # Worker sin TM_ARTIFACT_URL/TM_INTERNAL_TOKEN válidos: infraestructura.
        return AttemptOutcome(
            match_id=attempt.match.match_id, attempt_id=attempt.attempt_id,
            generation=attempt.generation, worker_id=worker_id,
            kind=OutcomeKind.INFRASTRUCTURE_FAILURE, winner_id="",
            reason="artifact_download_not_configured",
        ).to_json()
    return run_registered_match(attempt, store, worker_id=worker_id).to_json()


@dataclass
class _Entry:
    attempt: AttemptSpec
    ref: object
    node_id: str
    worker_id: str
    outcome: AttemptOutcome | None = None
    timer: threading.Timer | None = field(default=None, repr=False)


class RayExecution:
    def __init__(
        self,
        nodes: Callable[[], list[dict]],
        *,
        cancel_grace_ms: int,
        suspect_cooldown_ms: int = 45_000,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        import ray

        self._ray = ray
        self._nodes = nodes
        self._cancel_grace = cancel_grace_ms / 1000
        self._membership = WorkerMembership(suspect_cooldown_ms, clock)
        # Sin reintentos de Ray: ocultarían intentos que la plataforma debe ver.
        self._task = ray.remote(max_retries=0, retry_exceptions=False)(play_attempt)
        self._entries: dict[str, _Entry] = {}
        self._last_used: dict[str, int] = {}
        self._order = count(1)
        self._lock = threading.RLock()
        self._closed = False

    def submit(self, attempt: AttemptSpec) -> None:
        from ray.util.scheduling_strategies import NodeAffinitySchedulingStrategy

        with self._lock:
            if self._closed:
                raise ExecutionError("Backend Ray cerrado")
            previous = self._entries.get(attempt.attempt_id)
            if previous is not None:
                if previous.attempt != attempt:
                    raise ExecutionError("ID de intento reutilizado con otro contenido")
                return
            name = self._choose_worker()
            node_id = self._membership.node_id(name)
            try:
                ref = self._task.options(
                    num_cpus=1,
                    resources={"tm_worker": 1},
                    # soft=False: si el nodo desaparece, la tarea falla en vez de moverse.
                    scheduling_strategy=NodeAffinitySchedulingStrategy(
                        node_id=node_id, soft=False
                    ),
                ).remote(attempt.to_json(), name)
            except Exception as exc:
                raise ExecutionError(f"Ray rechazó el envío ({type(exc).__name__})") from exc
            self._entries[attempt.attempt_id] = _Entry(attempt, ref, node_id, name)
            self._last_used[name] = next(self._order)

    def poll(self) -> tuple[AttemptOutcome, ...]:
        with self._lock:
            self._collect()
            return tuple(e.outcome for e in self._entries.values() if e.outcome is not None)

    def acknowledge(self, attempt_id: str) -> None:
        with self._lock:
            entry = self._entries.pop(attempt_id, None)
            if entry is not None and entry.timer is not None:
                entry.timer.cancel()

    def cancel(self, attempt_id: str) -> None:
        with self._lock:
            entry = self._entries.get(attempt_id)
            if entry is None or entry.outcome is not None or entry.timer is not None:
                return
            # Parada suave: el árbitro recibe la interrupción y cierra a los bots.
            self._ray.cancel(entry.ref, force=False)
            entry.timer = threading.Timer(self._cancel_grace, self._force, (attempt_id,))
            entry.timer.daemon = True
            entry.timer.start()

    def workers(self) -> tuple[WorkerInfo, ...]:
        with self._lock:
            self._refresh()
            busy = self._busy()
            workers = []
            for name in self._membership.names():
                status = self._membership.status(name)
                # Un worker perdido no ofrece capacidad aunque Ray aún devuelva su intento.
                capacity = 0 if status is WorkerStatus.LOST else self._membership.slots(name)
                running = busy.get(self._membership.node_id(name), 0)
                workers.append(WorkerInfo(
                    worker_id=name, status=status, capacity=capacity,
                    running_attempts=min(running, capacity),
                ))
            return tuple(workers)

    def worker_of(self, attempt_id: str) -> str:
        with self._lock:
            entry = self._entries.get(attempt_id)
            return entry.worker_id if entry is not None else ""

    def close(self) -> None:
        with self._lock:
            self._closed = True
            for entry in self._entries.values():
                if entry.timer is not None:
                    entry.timer.cancel()
                if entry.outcome is None:
                    try:
                        self._ray.cancel(entry.ref, force=True)
                    except Exception:
                        logger.exception(
                            "No se pudo cancelar el intento %s", entry.attempt.attempt_id
                        )

    def _force(self, attempt_id: str) -> None:
        with self._lock:
            entry = self._entries.get(attempt_id)
            if entry is None or self._closed:
                return
            self._collect()
            if entry.outcome is not None:
                return
            # Mata el proceso del worker: los bots podrían quedar huérfanos (D1.4).
            logger.warning("Parada forzada del intento %s en %s", attempt_id, entry.worker_id)
            try:
                self._ray.cancel(entry.ref, force=True)
            except Exception:
                logger.exception("No se pudo forzar la parada del intento %s", attempt_id)

    def _collect(self) -> None:
        # Ray Client devuelve objetos de referencia nuevos: se emparejan por su ID.
        pending = {e.ref.hex(): e for e in self._entries.values() if e.outcome is None}
        if not pending:
            return
        refs = [e.ref for e in pending.values()]
        ready, _ = self._ray.wait(refs, num_returns=len(refs), timeout=0)
        for ref in ready:
            entry = pending[ref.hex()]
            entry.outcome = self._outcome(entry)

    def _outcome(self, entry: _Entry) -> AttemptOutcome:
        try:
            raw = self._ray.get(entry.ref, timeout=0)
        except Exception as exc:
            # Ray informa que la tarea murió. Si no lo pedimos nosotros (cancelación),
            # el worker queda sospechoso aunque Ray aún lo liste como vivo (D3.1).
            if entry.timer is None:
                self._membership.suspect(entry.worker_id)
            return self._failure(entry, f"ray:{type(exc).__name__}")
        try:
            outcome = AttemptOutcome.from_json(raw)
            outcome.validate_for(entry.attempt)
            return outcome
        except Exception as exc:
            # Resultado ilegible o ajeno: infraestructura, pero no prueba de un worker muerto.
            return self._failure(entry, f"invalid_outcome:{type(exc).__name__}")

    def _failure(self, entry: _Entry, reason: str) -> AttemptOutcome:
        logger.warning(
            "Intento %s terminó con %s en %s", entry.attempt.attempt_id, reason, entry.worker_id
        )
        return AttemptOutcome(
            match_id=entry.attempt.match.match_id,
            attempt_id=entry.attempt.attempt_id,
            generation=entry.attempt.generation,
            worker_id=entry.worker_id,
            kind=OutcomeKind.INFRASTRUCTURE_FAILURE,
            winner_id="",
            reason=reason,
        )

    def _busy(self) -> dict[str, int]:
        busy: dict[str, int] = {}
        for entry in self._entries.values():
            if entry.outcome is None:
                busy[entry.node_id] = busy.get(entry.node_id, 0) + 1
        return busy

    def _refresh(self) -> None:
        """Recoge resultados y actualiza la membresía con los workers vivos de Ray."""
        self._collect()
        try:
            nodes = self._nodes()
        except Exception as exc:
            raise ExecutionError("No se pudo consultar la membresía Ray") from exc
        self._membership.observe(
            (n["name"], n["node_id"], n["slots"]) for n in nodes if n["worker"] and n["slots"] > 0
        )

    def _choose_worker(self) -> str:
        self._refresh()
        busy = self._busy()
        free = [
            name for name in self._membership.names()
            if self._membership.status(name) is WorkerStatus.AVAILABLE
            and busy.get(self._membership.node_id(name), 0) < self._membership.slots(name)
        ]
        if not free:
            raise ExecutionError("Sin capacidad libre en los workers")
        # El menos usado recientemente: con un intento a la vez se alternan workers.
        return min(free, key=lambda name: self._last_used.get(name, 0))
