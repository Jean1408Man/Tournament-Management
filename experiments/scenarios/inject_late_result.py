"""Inyección de laboratorio (4.4, D4.4 b): entrega tarde el resultado de un intento.

Construye un resultado deportivo válido para un intento ya existente y lo pasa
por el mismo código de aceptación que usa el coordinador
(PersistentTournamentService.accept). No es un endpoint ni forma parte del
paquete: se ejecuta a mano dentro del coordinador, que tiene TM_DATABASE_URL.

    docker exec -i tm-coordinator python - ATTEMPT_ID < experiments/scenarios/inject_late_result.py

Imprime un JSON con el intento y si fue aceptado; el rechazo queda en el
historial del torneo como evento result.discarded.
"""

import json
import os
import sys

from tournament_management.database.unit_of_work import PostgresUnitOfWork
from tournament_management.shared.messages import AttemptOutcome, AttemptSpec, OutcomeKind
from tournament_management.tournaments.service import PersistentTournamentService


def main(attempt_id: str, database_url: str) -> dict:
    with PostgresUnitOfWork(database_url) as work:
        with work.matches.connection.cursor() as cursor:
            cursor.execute(
                "SELECT spec, outcome FROM attempts WHERE attempt_id = %s", (attempt_id,)
            )
            row = cursor.fetchone()
    if row is None:
        raise SystemExit(f"No existe el intento {attempt_id}")
    attempt = AttemptSpec.from_dict(row["spec"])
    worker = (row["outcome"] or {}).get("worker_id") or "lab-injection"
    late = AttemptOutcome(
        match_id=attempt.match.match_id,
        attempt_id=attempt.attempt_id,
        generation=attempt.generation,
        worker_id=worker,
        kind=OutcomeKind.COMPLETED,
        winner_id=attempt.match.competitor_ids[0],
        reason="lab_injection",
    )
    service = PersistentTournamentService(lambda: PostgresUnitOfWork(database_url))
    return {
        "attempt_id": attempt_id,
        "generation": attempt.generation,
        "accepted": service.accept(late),
    }


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit("Uso: python - ATTEMPT_ID < inject_late_result.py")
    print(json.dumps(main(sys.argv[1], os.environ["TM_DATABASE_URL"])))
