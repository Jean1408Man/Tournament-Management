"""Escenarios durables sin servidor HTTP, para observar cada límite transaccional."""

import hashlib
from dataclasses import dataclass
from uuid import uuid4

import pytest

from tournament_management.database.unit_of_work import PostgresUnitOfWork
from tournament_management.shared.messages import AttemptOutcome, AttemptSpec, OutcomeKind
from tournament_management.tournaments.model import TournamentSpec
from tournament_management.tournaments.service import (
    PersistentTournamentService,
)


@dataclass
class Scenario:
    database: str
    service: PersistentTournamentService
    spec: TournamentSpec

    def work(self):
        return PostgresUnitOfWork(self.database)

    def issue(self, match=None):
        match = match or self.service.pending(self.spec.tournament_id)[0]
        with self.work() as work:
            generation = work.matches.next_generation(match.match_id)
        attempt = AttemptSpec(
            attempt_id=str(uuid4()),
            generation=generation,
            match=match,
            turn_timeout_ms=100,
            match_timeout_ms=1000,
            memory_limit_bytes=1000000,
            output_limit_bytes=1024,
        )
        self.service.issue(attempt)
        return attempt

    def outcome(self, attempt):
        return AttemptOutcome(
            match_id=attempt.match.match_id,
            attempt_id=attempt.attempt_id,
            generation=attempt.generation,
            worker_id="previous-process",
            kind=OutcomeKind.COMPLETED,
            winner_id=attempt.match.competitor_ids[0],
            reason="last_move",
        )


@pytest.fixture
def scenario_factory(database):
    def create(identifier="t1", count=4, format_name="elimination"):
        hashes = tuple(hashlib.sha256(f"bot-{i}".encode()).hexdigest() for i in range(count))
        with PostgresUnitOfWork(database) as work:
            for index, artifact in enumerate(hashes):
                if work.registry.find_competitor(f"c{index}") is None:
                    work.registry.add_artifact(artifact, 100)
                    work.registry.add_competitor(f"c{index}", f"Bot {index}", artifact)
            work.commit()
        spec = TournamentSpec(
            tournament_id=identifier,
            format_name=format_name,
            competitor_ids=tuple(f"c{i}" for i in range(count)),
            artifact_hashes=hashes,
            initial_piles=(3, 5, 7),
            first_player=0,
            seed=42,
        )
        service = PersistentTournamentService(lambda: PostgresUnitOfWork(database))
        service.create(spec, idempotency_key=identifier)
        return Scenario(database, service, spec)

    return create
