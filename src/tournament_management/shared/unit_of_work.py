"""Contrato de la transacción que comparten los módulos; sin PostgreSQL ni Ray.

Cada módulo es dueño del SQL de sus tablas y la UoW concreta (``database/``)
reúne esos repositorios sobre una sola conexión. Solo hay una implementación
de cada repositorio, por eso aquí no se declaran sus interfaces.
"""

from typing import Any, Protocol, Self


class UnitOfWork(Protocol):
    registry: Any
    idempotency: Any
    matches: Any
    tournaments: Any
    events: Any

    def __enter__(self) -> Self: ...

    def commit(self) -> None:
        """Confirma conjuntamente aceptación, avance y eventos."""
        ...

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
        """Revierte cambios si hubo error o no se llamó a commit."""
        ...
