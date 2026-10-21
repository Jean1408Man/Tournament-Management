"""Repositorio PostgreSQL de eventos de dominio."""

from typing import Any

from tournament_management.application.ports import EventRepository
from tournament_management.domain.messages import DomainEvent


class _PostgresEvents(EventRepository):
    def __init__(self, connection: Any) -> None:
        self.connection = connection

    def append(self, event: DomainEvent) -> None:
        with self.connection.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO domain_events (event_id, tournament_id, sequence, payload)
                VALUES (%s, %s, %s, %s::jsonb)
                """,
                (event.event_id, event.tournament_id, event.sequence, event.to_json()),
            )
