"""Partidas e intentos: serialización con cancelación, reintentos y resultados."""

from typing import Any

from tournament_management.shared.messages import (
    AttemptOutcome,
    AttemptSpec,
    MatchSpec,
    OutcomeKind,
)
from tournament_management.shared.states import (
    AttemptState,
    MatchState,
    TournamentState,
    transition,
)
from tournament_management.tournaments.persistence.attempt_failures import PostgresAttemptFailures
from tournament_management.tournaments.persistence.match_queries import PostgresMatchQueries
from tournament_management.tournaments.persistence.match_recovery import PostgresMatchRecovery
from tournament_management.tournaments.persistence.tournaments import _PostgresTournaments


class _PostgresMatches(PostgresMatchQueries, PostgresAttemptFailures, PostgresMatchRecovery):
    def __init__(self, connection: Any) -> None:
        self.connection = connection

    def _lock(self, match_id: str) -> dict:
        # La especificación es inmutable; esta primera lectura solo localiza el torneo.
        match = self.get(match_id)
        tournament = _PostgresTournaments(self.connection).lock(match.tournament_id)
        with self.connection.cursor() as cursor:
            cursor.execute("SELECT * FROM matches WHERE match_id = %s FOR UPDATE", (match_id,))
            row = cursor.fetchone()
        return {**row, "tournament_state": tournament["state"]}

    def get(self, match_id: str) -> MatchSpec:
        with self.connection.cursor() as cursor:
            cursor.execute("SELECT spec FROM matches WHERE match_id = %s", (match_id,))
            row = cursor.fetchone()
        if row is None:
            raise KeyError(match_id)
        return MatchSpec.from_dict(row["spec"])

    def pending(self, tournament_id: str) -> tuple[MatchSpec, ...]:
        with self.connection.cursor() as cursor:
            cursor.execute(
                "SELECT spec FROM matches WHERE tournament_id = %s AND state = %s "
                "ORDER BY match_id",
                (tournament_id, MatchState.READY.value),
            )
            return tuple(MatchSpec.from_dict(row["spec"]) for row in cursor.fetchall())

    def current_attempt(self, match_id: str) -> AttemptSpec | None:
        with self.connection.cursor() as cursor:
            cursor.execute(
                """SELECT a.spec FROM matches m
                   LEFT JOIN attempts a ON a.attempt_id = m.current_attempt_id
                   WHERE m.match_id = %s""",
                (match_id,),
            )
            row = cursor.fetchone()
        if row is None:
            raise KeyError(match_id)
        return None if row["spec"] is None else AttemptSpec.from_dict(row["spec"])

    def add_attempt(self, attempt: AttemptSpec) -> None:
        """ID nuevo y generación creciente; invalida el intento anterior."""
        row = self._lock(attempt.match.match_id)
        if (
            MatchSpec.from_dict(row["spec"]) != attempt.match
            or row["tournament_state"] != TournamentState.RUNNING.value
            or row["state"] not in {MatchState.READY.value, MatchState.RUNNING.value}
            or row["accepted_attempt_id"] is not None
        ):
            raise ValueError("La partida no acepta más intentos")
        with self.connection.cursor() as cursor:
            # Se bloquea la partida incluso cuando aún no hay intentos. Bloquear
            # solo el último intento dejaba una carrera al emitir la generación 1.
            cursor.execute(
                "SELECT COALESCE(MAX(generation), 0) AS generation "
                "FROM attempts WHERE match_id = %s",
                (attempt.match.match_id,),
            )
            if attempt.generation <= cursor.fetchone()["generation"]:
                raise ValueError("La generación del intento debe aumentar")
            if row["current_attempt_id"]:
                cursor.execute(
                    """UPDATE attempts SET state = CASE WHEN state = %s THEN %s ELSE %s END
                       WHERE attempt_id = %s AND state IN (%s, %s)""",
                    (
                        AttemptState.QUEUED.value,
                        AttemptState.EXPIRED.value,
                        AttemptState.DISCARDED.value,
                        row["current_attempt_id"],
                        AttemptState.QUEUED.value,
                        AttemptState.RUNNING.value,
                    ),
                )
            cursor.execute(
                """INSERT INTO attempts (attempt_id, match_id, generation, state, spec)
                   VALUES (%s, %s, %s, %s, %s::jsonb)""",
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

    def try_accept(self, outcome: AttemptOutcome) -> bool:
        """CAS transaccional: solo intento vigente y partida/torneo activos.

        Valida identidad y ganador contra MatchSpec. Solo completed o bot_failure.
        False para duplicados, resultados viejos o cancelados; nunca sobrescribe.
        Debe serializarse también frente a cancelación e invalidación del intento.
        """
        try:
            row = self._lock(outcome.match_id)
        except KeyError:
            return False
        if (
            row["current_attempt_id"] != outcome.attempt_id
            or row["accepted_attempt_id"] is not None
            or row["state"] != MatchState.RUNNING.value
            or row["tournament_state"] != TournamentState.RUNNING.value
            or outcome.kind not in {OutcomeKind.COMPLETED, OutcomeKind.BOT_FAILURE}
        ):
            return False
        with self.connection.cursor() as cursor:
            cursor.execute(
                "SELECT state, spec FROM attempts WHERE attempt_id = %s FOR UPDATE",
                (outcome.attempt_id,),
            )
            attempt_row = cursor.fetchone()
            if attempt_row["state"] != AttemptState.RUNNING.value:
                return False
            try:
                outcome.validate_for(AttemptSpec.from_dict(attempt_row["spec"]))
            except ValueError:
                return False
            cursor.execute(
                "UPDATE attempts SET state = %s, outcome = %s::jsonb WHERE attempt_id = %s",
                (AttemptState.ACCEPTED.value, outcome.to_json(), outcome.attempt_id),
            )
            cursor.execute(
                """INSERT INTO results (match_id, attempt_id, tournament_id, sequence, outcome)
                   SELECT %s, %s, %s, COALESCE(MAX(sequence), 0) + 1, %s::jsonb
                   FROM results WHERE tournament_id = %s""",
                (
                    outcome.match_id,
                    outcome.attempt_id,
                    row["tournament_id"],
                    outcome.to_json(),
                    row["tournament_id"],
                ),
            )
        return True

    def rejection_reason(self, outcome: AttemptOutcome) -> str:
        """Por qué try_accept rechazó el resultado; misma transacción, mismos bloqueos."""
        if outcome.kind not in {OutcomeKind.COMPLETED, OutcomeKind.BOT_FAILURE}:
            return "not_a_result"
        try:
            row = self._lock(outcome.match_id)
        except KeyError:
            return "unknown_match"
        if row["accepted_attempt_id"] == outcome.attempt_id:
            return "duplicate"
        # Otro intento ya ganó la partida: este llega tarde aunque el torneo haya terminado.
        if row["accepted_attempt_id"] is not None:
            return "stale_attempt"
        if row["tournament_state"] != TournamentState.RUNNING.value:
            return "tournament_not_running"
        if row["current_attempt_id"] != outcome.attempt_id:
            return "stale_attempt"
        return "invalid"

    def accepted(self, match_id: str) -> AttemptOutcome | None:
        with self.connection.cursor() as cursor:
            cursor.execute("SELECT outcome FROM results WHERE match_id = %s", (match_id,))
            row = cursor.fetchone()
        return None if row is None else AttemptOutcome.from_dict(row["outcome"])

    def invalidate(self, match_id: str, attempt_id: str) -> bool:
        row = self._lock(match_id)
        if (
            row["current_attempt_id"] != attempt_id
            or row["accepted_attempt_id"] is not None
            or row["state"] != MatchState.RUNNING.value
        ):
            return False
        with self.connection.cursor() as cursor:
            cursor.execute(
                "UPDATE attempts SET state = %s WHERE attempt_id = %s AND state IN (%s, %s)",
                (
                    AttemptState.EXPIRED.value,
                    attempt_id,
                    AttemptState.QUEUED.value,
                    AttemptState.RUNNING.value,
                ),
            )
            cursor.execute(
                "UPDATE matches SET state = %s, current_attempt_id = NULL WHERE match_id = %s",
                (MatchState.READY.value, match_id),
            )
        return True

    def set_state(self, match_id: str, state: MatchState) -> None:
        row = self._lock(match_id)
        transition(MatchState(row["state"]), state)
        with self.connection.cursor() as cursor:
            cursor.execute("UPDATE matches SET state = %s WHERE match_id = %s", (state, match_id))

    def set_attempt_state(self, attempt_id: str, state: AttemptState) -> None:
        with self.connection.cursor() as cursor:
            cursor.execute("SELECT match_id FROM attempts WHERE attempt_id = %s", (attempt_id,))
            row = cursor.fetchone()
        if row is None:
            raise KeyError(attempt_id)
        self._lock(row["match_id"])
        with self.connection.cursor() as cursor:
            cursor.execute(
                "SELECT state FROM attempts WHERE attempt_id = %s FOR UPDATE", (attempt_id,)
            )
            transition(AttemptState(cursor.fetchone()["state"]), state)
            cursor.execute(
                "UPDATE attempts SET state = %s WHERE attempt_id = %s", (state, attempt_id)
            )

    def start(self, match_id: str) -> None:
        row = self._lock(match_id)
        if row["state"] == MatchState.READY.value:
            self.set_state(match_id, MatchState.RUNNING)
        elif row["state"] != MatchState.RUNNING.value:
            raise ValueError("La partida no puede iniciarse")

    def cancel_pending(self, tournament_id: str) -> None:
        _PostgresTournaments(self.connection).lock(tournament_id)
        with self.connection.cursor() as cursor:
            cursor.execute(
                """UPDATE attempts SET state = %s WHERE match_id IN
                   (SELECT match_id FROM matches WHERE tournament_id = %s)
                   AND state IN (%s, %s)""",
                (
                    AttemptState.CANCELLED.value,
                    tournament_id,
                    AttemptState.QUEUED.value,
                    AttemptState.RUNNING.value,
                ),
            )
            cursor.execute(
                """UPDATE matches SET state = %s, current_attempt_id = NULL
                   WHERE tournament_id = %s AND state IN (%s, %s, %s, %s)""",
                (
                    MatchState.CANCELLED.value,
                    tournament_id,
                    MatchState.WAITING.value,
                    MatchState.READY.value,
                    MatchState.RUNNING.value,
                    MatchState.BLOCKED.value,
                ),
            )
