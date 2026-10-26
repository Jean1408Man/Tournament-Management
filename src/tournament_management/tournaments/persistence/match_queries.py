"""Lecturas para HTTP, separadas de la lógica de aceptación de resultados."""

from typing import Any


class PostgresMatchQueries:
    connection: Any

    def summary(self, tournament_id: str) -> dict:
        with self.connection.cursor() as cursor:
            cursor.execute(
                """SELECT state, count(*) AS count FROM matches
                   WHERE tournament_id = %s GROUP BY state""",
                (tournament_id,),
            )
            counts = {row["state"]: row["count"] for row in cursor.fetchall()}
            cursor.execute(
                """SELECT count(*) AS count FROM attempts a JOIN matches m USING (match_id)
                   WHERE m.tournament_id = %s""",
                (tournament_id,),
            )
            attempts = cursor.fetchone()["count"]
        return {
            "matches_total": sum(counts.values()),
            "attempts_total": attempts,
            **{
                state: counts.get(state, 0)
                for state in (
                    "waiting",
                    "ready",
                    "running",
                    "completed",
                    "blocked",
                    "cancelled",
                )
            },
        }

    def page(self, tournament_id: str, *, offset: int, limit: int) -> list[dict]:
        with self.connection.cursor() as cursor:
            cursor.execute(
                """SELECT * FROM matches WHERE tournament_id = %s
                   ORDER BY created_at, match_id LIMIT %s OFFSET %s""",
                (tournament_id, limit, offset),
            )
            rows = cursor.fetchall()
            if not rows:
                return []
            cursor.execute(
                """SELECT attempt_id, match_id, generation, state, outcome FROM attempts
                   WHERE match_id = ANY(%s) ORDER BY generation""",
                ([row["match_id"] for row in rows],),
            )
            attempts: dict[str, list] = {}
            for row in cursor.fetchall():
                match_id = row.pop("match_id")
                attempts.setdefault(match_id, []).append(row)
        return [
            {
                "match_id": row["match_id"],
                "state": row["state"],
                "spec": row["spec"],
                "winner_id": row["winner_id"],
                "current_attempt_id": row["current_attempt_id"],
                "accepted_attempt_id": row["accepted_attempt_id"],
                "attempts": attempts.get(row["match_id"], []),
            }
            for row in rows
        ]

    def next_generation(self, match_id: str) -> int:
        with self.connection.cursor() as cursor:
            cursor.execute(
                "SELECT COALESCE(MAX(generation), 0) + 1 AS generation FROM attempts "
                "WHERE match_id = %s",
                (match_id,),
            )
            return cursor.fetchone()["generation"]

    def bracket_rows(self, tournament_id: str) -> list[dict]:
        with self.connection.cursor() as cursor:
            cursor.execute(
                "SELECT match_id, state, winner_id FROM matches WHERE tournament_id = %s",
                (tournament_id,),
            )
            return cursor.fetchall()
