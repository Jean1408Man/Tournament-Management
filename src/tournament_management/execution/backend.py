"""Puerto de ejecución: local hoy, Ray en la etapa 4 y nativo en la Fase 2."""

from typing import Protocol

from tournament_management.shared.messages import AttemptOutcome, AttemptSpec, WorkerInfo


class ExecutionError(Exception):
    """Fallo de transporte/ejecución traducido por el adaptador."""


class ExecutionBackend(Protocol):
    def submit(self, attempt: AttemptSpec) -> None:
        """Mismo attempt_id/contenido: no crear otra ejecución; distinto: error.

        Un envío de resultado incierto puede repetirse con el mismo identificador.
        Un reintento lógico usa siempre otro ID y una generación mayor.
        """
        ...

    def poll(self) -> tuple[AttemptOutcome, ...]:
        """Consulta no bloqueante; puede devolver notificaciones repetidas.

        Consultar no reconoce durabilidad. Retener hasta acknowledge o pérdida
        del backend; los intentos perdidos se recuperarán mediante plazos.
        """
        ...

    def acknowledge(self, attempt_id: str) -> None:
        """Olvidar notificación solo después de procesarla durablemente; idempotente."""
        ...

    def cancel(self, attempt_id: str) -> None:
        """Petición idempotente de mejor esfuerzo; aún puede llegar un resultado."""
        ...

    def workers(self) -> tuple[WorkerInfo, ...]:
        """Instantánea de identidades propias, no objetos del middleware."""
        ...
