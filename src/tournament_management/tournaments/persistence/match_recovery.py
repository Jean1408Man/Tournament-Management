"""SQL de recuperación; el servicio mantiene el bloqueo del torneo hasta commit."""

from typing import Any


class PostgresMatchRecovery:
    connection: Any

    def recovery_rows(self, tournament_id: str) -> list[dict]:
        """Partidas sin resolver y última generación, bajo el bloqueo del torneo."""
        with self.connection.cursor() as cursor:
            cursor.execute(
                """SELECT m.match_id, m.current_attempt_id,
                          COALESCE(MAX(a.generation), 0) AS generation
                   FROM matches m LEFT JOIN attempts a USING (match_id)
                   WHERE m.tournament_id = %s AND m.state IN ('ready', 'running')
                         AND m.accepted_attempt_id IS NULL
                   GROUP BY m.match_id ORDER BY m.created_at, m.match_id""",
                (tournament_id,),
            )
            return cursor.fetchall()

    def lose_current(self, match_id: str) -> str | None:
        """Cierra como perdido el intento anterior y libera su partida para reemplazo."""
        with self.connection.cursor() as cursor:
            cursor.execute(
                """SELECT current_attempt_id FROM matches
                   WHERE match_id = %s AND accepted_attempt_id IS NULL FOR UPDATE""",
                (match_id,),
            )
            row = cursor.fetchone()
            if row is None or row["current_attempt_id"] is None:
                return None
            attempt_id = row["current_attempt_id"]
            cursor.execute(
                """UPDATE attempts SET state = 'lost'
                   WHERE attempt_id = %s AND state IN ('queued', 'running')""",
                (attempt_id,),
            )
            if cursor.rowcount != 1:
                raise ValueError("El intento vigente no está activo durante recuperación")
            cursor.execute(
                "UPDATE matches SET state = 'ready', current_attempt_id = NULL WHERE match_id = %s",
                (match_id,),
            )
            return attempt_id
