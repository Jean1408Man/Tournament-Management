"""Repositorio PostgreSQL de eventos de dominio."""

from typing import Any

from tournament_management.shared.messages import DomainEvent


class _PostgresEvents:
    def __init__(self, connection: Any) -> None:
        self.connection = connection

    def append(self, event: DomainEvent) -> None:
        """ID y secuencia por torneo únicos; visible externamente tras commit."""
        with self.connection.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO domain_events (event_id, tournament_id, sequence, payload)
                VALUES (%s, %s, %s, %s::jsonb)
                """,
                (event.event_id, event.tournament_id, event.sequence, event.to_json()),
            )

    def next_sequence(self, tournament_id: str) -> int:
        with self.connection.cursor() as cursor:
            # Mismo bloqueo que tournaments.persistence.lock(): la secuencia se
            # serializa por torneo sin que este módulo dependa de tournaments/.
            cursor.execute(
                "SELECT 1 FROM tournaments WHERE tournament_id = %s FOR UPDATE", (tournament_id,)
            )
            if cursor.fetchone() is None:
                raise KeyError(tournament_id)
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
