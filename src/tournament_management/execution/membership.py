"""Membresía propia de workers: available, suspected y lost (etapa 4.3, D3.1).

Lógica pura, sin middleware ni reloj real: el backend Ray le pasa lo que ve y
lo que observa; el backend nativo de la Fase 2 podrá pasarle sus heartbeats.

- Un worker visto antes que ya no aparece vivo está perdido (lost).
- Un fallo de infraestructura mientras el middleware aún lo da por vivo lo
  vuelve sospechoso (suspected) durante un enfriamiento: Ray tarda ~30 s en
  confirmar una muerte que la tarea ya informó.
- Deja de ser sospechoso al pasar el enfriamiento o al reaparecer reiniciado
  (mismo nombre, otra identidad interna).
"""

import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass

from tournament_management.shared.messages import WorkerStatus


@dataclass
class _Worker:
    node_id: str
    slots: int
    alive: bool = True
    suspected_until: float | None = None


class WorkerMembership:
    def __init__(self, cooldown_ms: int, clock: Callable[[], float] = time.monotonic) -> None:
        self._cooldown = cooldown_ms / 1000
        self._clock = clock
        self._workers: dict[str, _Worker] = {}

    def observe(self, alive: Iterable[tuple[str, str, int]]) -> None:
        """Instantánea de workers vivos como (nombre, identidad interna, huecos)."""
        seen = set()
        for name, node_id, slots in alive:
            seen.add(name)
            worker = self._workers.get(name)
            if worker is None or worker.node_id != node_id:
                # Nuevo o reiniciado: no hereda la sospecha del proceso anterior.
                self._workers[name] = _Worker(node_id, slots)
            else:
                worker.alive, worker.slots = True, slots
        for name, worker in self._workers.items():
            if name not in seen:
                worker.alive = False

    def suspect(self, name: str) -> None:
        worker = self._workers.get(name)
        if worker is not None and worker.alive:
            worker.suspected_until = self._clock() + self._cooldown

    def status(self, name: str) -> WorkerStatus:
        worker = self._workers[name]
        if not worker.alive:
            return WorkerStatus.LOST
        if worker.suspected_until is not None:
            if self._clock() < worker.suspected_until:
                return WorkerStatus.SUSPECTED
            worker.suspected_until = None
        return WorkerStatus.AVAILABLE

    def node_id(self, name: str) -> str:
        return self._workers[name].node_id

    def slots(self, name: str) -> int:
        return self._workers[name].slots

    def names(self) -> list[str]:
        return sorted(self._workers)
