"""Unidad de trabajo PostgreSQL y punto de entrada de persistencia."""

from __future__ import annotations

from typing import Any

from tournament_management.competitors.repository import _PostgresRegistry
from tournament_management.database.connection import (
    check_connection as check_connection,
)
from tournament_management.database.connection import driver
from tournament_management.database.connection import migrate as migrate
from tournament_management.database.idempotency import _PostgresIdempotency
from tournament_management.events.repository import _PostgresEvents
from tournament_management.tournaments.persistence.matches import _PostgresMatches
from tournament_management.tournaments.persistence.tournaments import _PostgresTournaments


class PostgresUnitOfWork:
    """Unidad de trabajo explícita; cerrar sin ``commit`` siempre revierte."""

    def __init__(self, database_url: str) -> None:
        self._database_url = database_url
        self._connection: Any | None = None
        self._committed = False

    def __enter__(self) -> PostgresUnitOfWork:
        if self._connection is not None:
            raise RuntimeError("La unidad de trabajo ya está abierta")
        self._committed = False
        psycopg, dict_row = driver()
        try:
            self._connection = psycopg.connect(
                self._database_url, row_factory=dict_row, connect_timeout=5
            )
        except psycopg.Error as exc:
            raise OSError("PostgreSQL no disponible") from exc
        self.matches = _PostgresMatches(self._connection)
        self.tournaments = _PostgresTournaments(self._connection)
        self.events = _PostgresEvents(self._connection)
        self.registry = _PostgresRegistry(self._connection)
        self.idempotency = _PostgresIdempotency(self._connection)
        return self

    def commit(self) -> None:
        if self._connection is None:
            raise RuntimeError("La unidad de trabajo no está abierta")
        self._connection.commit()
        self._committed = True

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
        if self._connection is None:
            return
        try:
            if exc_type is not None or not self._committed:
                self._connection.rollback()
        finally:
            self._connection.close()
            self._connection = None
