"""Persistencia del cuadro y clasificación bajo el bloqueo del torneo."""

import json
from typing import Any

from tournament_management.application.ports import TournamentRepository
from tournament_management.domain.messages import AttemptOutcome, MatchSpec
from tournament_management.domain.persistent_tournament import TournamentSpec, progress, rebuild
from tournament_management.domain.states import MatchState, TournamentState, transition


class _PostgresTournaments(TournamentRepository):
    def __init__(self, connection: Any) -> None:
        self.connection = connection

    def recoverable_ids(self) -> tuple[str, ...]:
        with self.connection.cursor() as cursor:
            cursor.execute(
                "SELECT tournament_id FROM tournaments "
                "WHERE state = %s AND definition IS NOT NULL "
                "ORDER BY created_at, tournament_id",
                (TournamentState.RUNNING.value,),
            )
            return tuple(row["tournament_id"] for row in cursor.fetchall())

    def lock(self, tournament_id: str) -> dict:
        """Orden global de bloqueo: torneo → partida → intento.

        También se usa al cancelar y al emitir intentos. Serializar por torneo
        simplifica la corrección del avance; limita el paralelismo de escrituras,
        pero no mantiene una transacción abierta mientras se ejecutan bots.
        """
        with self.connection.cursor() as cursor:
            cursor.execute(
                "SELECT * FROM tournaments WHERE tournament_id = %s FOR UPDATE", (tournament_id,)
            )
            row = cursor.fetchone()
        if row is None:
            raise KeyError(tournament_id)
        return row

    def create(self, spec: TournamentSpec) -> None:
        tournament = rebuild(spec, ())
        with self.connection.cursor() as cursor:
            cursor.execute(
                """INSERT INTO tournaments (tournament_id, state, definition, progress)
                   VALUES (%s, %s, %s::jsonb, %s::jsonb)""",
                (
                    spec.tournament_id,
                    tournament.status,
                    spec.to_json(),
                    json.dumps(progress(tournament)),
                ),
            )
            for position, (competitor, artifact) in enumerate(
                zip(
                    spec.competitor_ids,
                    spec.artifact_hashes,
                    strict=True,
                )
            ):
                cursor.execute(
                    """INSERT INTO participants
                       (tournament_id, competitor_id, artifact_hash, position)
                       VALUES (%s, %s, %s, %s)""",
                    (spec.tournament_id, competitor, artifact, position),
                )
        self._schedule(spec, tournament)

    def get(self, tournament_id: str) -> dict:
        with self.connection.cursor() as cursor:
            cursor.execute("SELECT * FROM tournaments WHERE tournament_id = %s", (tournament_id,))
            row = cursor.fetchone()
        if row is None:
            raise KeyError(tournament_id)
        return row

    def state(self, tournament_id: str) -> TournamentState:
        return TournamentState(self.get(tournament_id)["state"])

    def set_state(self, tournament_id: str, state: TournamentState) -> None:
        row = self.lock(tournament_id)
        transition(TournamentState(row["state"]), state)
        with self.connection.cursor() as cursor:
            cursor.execute(
                "UPDATE tournaments SET state = %s WHERE tournament_id = %s",
                (state.value, tournament_id),
            )

    def advance(self, match: MatchSpec, outcome: AttemptOutcome) -> None:
        """Confirma la partida y materializa las nuevas parejas en la misma UoW."""
        row = self.lock(match.tournament_id)
        if row["definition"] is None:
            raise ValueError("El torneo antiguo no tiene definición durable")
        spec = TournamentSpec.from_dict(row["definition"])
        with self.connection.cursor() as cursor:
            cursor.execute(
                """UPDATE matches SET state = %s, winner_id = %s, accepted_attempt_id = %s
                   WHERE match_id = %s AND accepted_attempt_id IS NULL
                   AND EXISTS (SELECT 1 FROM results WHERE match_id = %s AND attempt_id = %s)""",
                (
                    MatchState.COMPLETED.value,
                    outcome.winner_id,
                    outcome.attempt_id,
                    match.match_id,
                    match.match_id,
                    outcome.attempt_id,
                ),
            )
            if cursor.rowcount != 1:
                raise ValueError("La partida ya avanzó o no tiene un resultado aceptado")
            cursor.execute(
                "SELECT outcome FROM results WHERE tournament_id = %s ORDER BY sequence",
                (match.tournament_id,),
            )
            results = tuple(AttemptOutcome.from_dict(item["outcome"]) for item in cursor.fetchall())
        tournament = rebuild(spec, results)
        self._schedule(spec, tournament)
        with self.connection.cursor() as cursor:
            cursor.execute(
                "UPDATE tournaments SET state = %s, progress = %s::jsonb WHERE tournament_id = %s",
                (tournament.status, json.dumps(progress(tournament)), spec.tournament_id),
            )

    def _schedule(self, spec: TournamentSpec, tournament: Any) -> None:
        with self.connection.cursor() as cursor:
            for pair in tournament.pending():
                match = spec.match(pair.match_id, pair.competitors)
                cursor.execute(
                    """INSERT INTO matches (match_id, tournament_id, state, spec)
                       VALUES (%s, %s, %s, %s::jsonb) ON CONFLICT (match_id) DO NOTHING""",
                    (match.match_id, spec.tournament_id, MatchState.READY.value, match.to_json()),
                )
