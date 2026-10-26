"""Cierre durable de intentos sin resultado deportivo aceptable."""

from typing import Any

from tournament_management.shared.messages import AttemptOutcome, AttemptSpec, OutcomeKind
from tournament_management.shared.states import AttemptState, MatchState, TournamentState


class PostgresAttemptFailures:
    connection: Any

    def finish_failure(self, outcome: AttemptOutcome, *, retry: bool) -> bool:
        if outcome.kind not in {OutcomeKind.INFRASTRUCTURE_FAILURE, OutcomeKind.NO_WINNER}:
            raise ValueError("Solo se cierran aquí intentos sin ganador deportivo")
        if retry and outcome.kind == OutcomeKind.NO_WINNER:
            raise ValueError("Un resultado no_winner no se reintenta")
        row = self._lock(outcome.match_id)
        if (
            row["tournament_state"] != TournamentState.RUNNING.value
            or row["state"] != MatchState.RUNNING.value
            or row["current_attempt_id"] != outcome.attempt_id
        ):
            return False
        with self.connection.cursor() as cursor:
            cursor.execute(
                "SELECT state, spec FROM attempts WHERE attempt_id = %s FOR UPDATE",
                (outcome.attempt_id,),
            )
            attempt = cursor.fetchone()
            if attempt["state"] != AttemptState.RUNNING.value:
                return False
            outcome.validate_for(AttemptSpec.from_dict(attempt["spec"]))
            cursor.execute(
                "UPDATE attempts SET state = %s, outcome = %s::jsonb WHERE attempt_id = %s",
                (AttemptState.FAILED.value, outcome.to_json(), outcome.attempt_id),
            )
            cursor.execute(
                "UPDATE matches SET state = %s, current_attempt_id = NULL WHERE match_id = %s",
                (MatchState.READY.value if retry else MatchState.BLOCKED.value, outcome.match_id),
            )
        return True

    def block_pending(self, tournament_id: str) -> None:
        with self.connection.cursor() as cursor:
            cursor.execute(
                "UPDATE matches SET state = %s WHERE tournament_id = %s AND state IN (%s, %s)",
                (
                    MatchState.BLOCKED.value,
                    tournament_id,
                    MatchState.WAITING.value,
                    MatchState.READY.value,
                ),
            )
