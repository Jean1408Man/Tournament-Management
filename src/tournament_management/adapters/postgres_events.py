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

    def next_sequence(self, tournament_id: str) -> int:
        from tournament_management.adapters.postgres_tournaments import _PostgresTournaments

        _PostgresTournaments(self.connection).lock(tournament_id)
        with self.connection.cursor() as cursor:
            cursor.execute(
                "SELECT COALESCE(MAX(sequence), 0) + 1 AS sequence "
                "FROM domain_events WHERE tournament_id = %s",
                (tournament_id,),
            )
            return cursor.fetchone()["sequence"]

    def list(
        self,
        tournament_id: str,
        after_sequence: int = 0,
        *,
        limit: int = 1000,
    ) -> tuple[DomainEvent, ...]:
        with self.connection.cursor() as cursor:
            cursor.execute(
                "SELECT payload FROM domain_events WHERE tournament_id = %s "
                "AND sequence > %s ORDER BY sequence LIMIT %s",
                (tournament_id, after_sequence, limit),
            )
            return tuple(DomainEvent.from_dict(row["payload"]) for row in cursor.fetchall())

    def latest_sequence(self, tournament_id: str) -> int:
        with self.connection.cursor() as cursor:
            cursor.execute(
                "SELECT COALESCE(MAX(sequence), 0) AS sequence FROM domain_events "
                "WHERE tournament_id = %s",
                (tournament_id,),
            )
            return cursor.fetchone()["sequence"]
