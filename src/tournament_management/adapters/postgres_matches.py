"""Repositorio PostgreSQL de partidas e intentos."""

from typing import Any

from tournament_management.application.ports import MatchRepository
from tournament_management.domain.messages import AttemptOutcome, AttemptSpec, MatchSpec
from tournament_management.domain.states import AttemptState, MatchState, TournamentState


class _PostgresMatches(MatchRepository):
    def __init__(self, connection: Any) -> None:
        self.connection = connection

    def get(self, match_id: str) -> MatchSpec:
        with self.connection.cursor() as cursor:
            cursor.execute("SELECT spec FROM matches WHERE match_id = %s", (match_id,))
            row = cursor.fetchone()
        if row is None:
            raise KeyError(match_id)
        return MatchSpec.from_dict(row["spec"])

    def current_attempt(self, match_id: str) -> AttemptSpec | None:
        with self.connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT a.spec FROM matches m
                LEFT JOIN attempts a ON a.attempt_id = m.current_attempt_id
                WHERE m.match_id = %s
                """,
                (match_id,),
            )
            row = cursor.fetchone()
        if row is None:
            raise KeyError(match_id)
        return None if row["spec"] is None else AttemptSpec.from_dict(row["spec"])

    def add_attempt(self, attempt: AttemptSpec) -> None:
        with self.connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT generation FROM attempts WHERE match_id = %s
                ORDER BY generation DESC LIMIT 1 FOR UPDATE
                """,
                (attempt.match.match_id,),
            )
            previous = cursor.fetchone()
            if previous is not None and attempt.generation <= previous["generation"]:
                raise ValueError("La generación del intento debe aumentar")
            cursor.execute(
                """
                INSERT INTO attempts (attempt_id, match_id, generation, state, spec)
                VALUES (%s, %s, %s, %s, %s::jsonb)
                """,
                (
                    attempt.attempt_id,
                    attempt.match.match_id,
                    attempt.generation,
                    AttemptState.QUEUED.value,
                    attempt.to_json(),
                ),
            )
            cursor.execute(
                "UPDATE matches SET current_attempt_id = %s WHERE match_id = %s",
                (attempt.attempt_id, attempt.match.match_id),
            )
            if cursor.rowcount != 1:
                raise KeyError(attempt.match.match_id)

    def try_accept(self, outcome: AttemptOutcome) -> bool:
        """Acepta solo el intento vigente y bloquea la fila de la partida."""
        with self.connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT m.current_attempt_id, m.state AS match_state, t.state AS tournament_state,
                       a.generation, a.state AS attempt_state, a.spec
                FROM matches m
                JOIN tournaments t ON t.tournament_id = m.tournament_id
                JOIN attempts a ON a.attempt_id = %s
                WHERE m.match_id = %s
                FOR UPDATE OF m, a, t
                """,
                (outcome.attempt_id, outcome.match_id),
            )
            row = cursor.fetchone()
            if row is None or row["current_attempt_id"] != outcome.attempt_id:
                return False
            if row["generation"] != outcome.generation:
                return False
            if (
                row["match_state"] != MatchState.RUNNING.value
                or row["tournament_state"] != TournamentState.RUNNING.value
            ):
                return False
            if row["attempt_state"] != AttemptState.RUNNING.value:
                return False
            attempt = AttemptSpec.from_dict(row["spec"])
            try:
                outcome.validate_for(attempt)
            except ValueError:
                return False
            if outcome.kind.value not in {"completed", "bot_failure"}:
                return False
            cursor.execute(
                """
                UPDATE attempts SET state = %s, outcome = %s::jsonb
                WHERE attempt_id = %s AND state = %s
                """,
                (
                    AttemptState.ACCEPTED.value,
                    outcome.to_json(),
                    outcome.attempt_id,
                    AttemptState.RUNNING.value,
                ),
            )
            return cursor.rowcount == 1

    def set_state(self, match_id: str, state: MatchState) -> None:
        self._set("matches", "match_id", match_id, state.value)

    def set_attempt_state(self, attempt_id: str, state: AttemptState) -> None:
        self._set("attempts", "attempt_id", attempt_id, state.value)

    def _set(self, table: str, key: str, identifier: str, state: str) -> None:
        with self.connection.cursor() as cursor:
            cursor.execute(f"UPDATE {table} SET state = %s WHERE {key} = %s", (state, identifier))
            if cursor.rowcount != 1:
                raise KeyError(identifier)

