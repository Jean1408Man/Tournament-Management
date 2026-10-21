"""Repositorio PostgreSQL de estado y avance de torneos."""

from typing import Any

from tournament_management.application.ports import TournamentRepository
from tournament_management.domain.messages import AttemptOutcome, MatchSpec
from tournament_management.domain.states import MatchState, TournamentState


class _PostgresTournaments(TournamentRepository):
    def __init__(self, connection: Any) -> None:
        self.connection = connection

    def state(self, tournament_id: str) -> TournamentState:
        with self.connection.cursor() as cursor:
            cursor.execute(
                "SELECT state FROM tournaments WHERE tournament_id = %s", (tournament_id,)
            )
            row = cursor.fetchone()
        if row is None:
            raise KeyError(tournament_id)
        return TournamentState(row["state"])

    def set_state(self, tournament_id: str, state: TournamentState) -> None:
        with self.connection.cursor() as cursor:
            cursor.execute(
                "UPDATE tournaments SET state = %s WHERE tournament_id = %s",
                (state.value, tournament_id),
            )
            if cursor.rowcount != 1:
                raise KeyError(tournament_id)

    def advance(self, match: MatchSpec, outcome: AttemptOutcome) -> None:
        """Registra el ganador una vez; el formato decide después la siguiente partida."""
        with self.connection.cursor() as cursor:
            cursor.execute(
                """
                UPDATE matches SET state = %s, winner_id = %s, accepted_attempt_id = %s
                WHERE match_id = %s AND accepted_attempt_id IS NULL
                """,
                (MatchState.COMPLETED.value, outcome.winner_id, outcome.attempt_id, match.match_id),
            )
            if cursor.rowcount != 1:
                raise ValueError("La partida ya fue avanzada o no existe")

