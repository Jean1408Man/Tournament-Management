"""Carreras y fallos contra PostgreSQL real; sin simular transacciones con mocks."""

import hashlib
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from importlib.resources import files
from threading import Barrier

import psycopg
import pytest

from tournament_management.database.connection import migrate
from tournament_management.database.unit_of_work import PostgresUnitOfWork
from tournament_management.events.repository import _PostgresEvents
from tournament_management.shared.messages import AttemptOutcome, AttemptSpec, OutcomeKind
from tournament_management.tournaments.model import TournamentSpec
from tournament_management.tournaments.service import (
    PersistentTournamentService,
)


def factory(database):
    return lambda: PostgresUnitOfWork(database)


def create(database, format_name="elimination", count=4, tournament_id="t1"):
    competitors = tuple(f"c{i}" for i in range(count))
    hashes = tuple(hashlib.sha256(c.encode()).hexdigest() for c in competitors)
    with PostgresUnitOfWork(database) as work:
        for competitor, artifact in zip(competitors, hashes, strict=True):
            work.registry.add_artifact(artifact, 100)
            work.registry.add_competitor(competitor, competitor, artifact)
        work.commit()
    spec = TournamentSpec(
        tournament_id=tournament_id,
        format_name=format_name,
        competitor_ids=competitors,
        artifact_hashes=hashes,
        initial_piles=(3, 5, 7),
        first_player=0,
        seed=42,
    )
    service = PersistentTournamentService(factory(database))
    service.create(spec, idempotency_key=tournament_id)
    return service, spec


def issue(service, match=None, generation=1):
    match = match or service.pending("t1")[0]
    attempt = AttemptSpec(
        attempt_id=f"{match.match_id}-a{generation}",
        generation=generation,
        match=match,
        turn_timeout_ms=100,
        match_timeout_ms=1000,
        memory_limit_bytes=1000000,
        output_limit_bytes=1024,
    )
    service.issue(attempt)
    return attempt


def outcome(attempt):
    return AttemptOutcome(
        match_id=attempt.match.match_id,
        attempt_id=attempt.attempt_id,
        generation=attempt.generation,
        worker_id="w1",
        kind=OutcomeKind.COMPLETED,
        winner_id=attempt.match.competitor_ids[0],
        reason="last_move",
    )


def race(*operations):
    barrier = Barrier(len(operations))

    def run(operation):
        barrier.wait(timeout=10)
        return operation()

    with ThreadPoolExecutor(max_workers=len(operations)) as executor:
        futures = [executor.submit(run, operation) for operation in operations]
        return [future.result(timeout=15) for future in futures]


def scalar(database, sql):
    with psycopg.connect(database) as connection:
        return connection.execute(sql).fetchone()[0]


@pytest.mark.parametrize("kind", [OutcomeKind.COMPLETED, OutcomeKind.BOT_FAILURE])
def test_concurrent_results_accept_once_and_advance_once(database, kind):
    service, _ = create(database, count=2)
    result = replace(outcome(issue(service)), kind=kind)
    assert sorted(race(lambda: service.accept(result), lambda: service.accept(result))) == [
        False,
        True,
    ]
    assert scalar(database, "SELECT count(*) FROM results") == 1
    assert scalar(database, "SELECT count(*) FROM attempts WHERE state = 'accepted'") == 1
    assert (
        scalar(
            database,
            "SELECT count(*) FROM domain_events WHERE payload->>'event_type' = 'result.accepted'",
        )
        == 1
    )
    with PostgresUnitOfWork(database) as work:
        row = work.tournaments.get("t1")
        assert row["state"] == "completed"
        assert row["progress"]["champion"] == result.winner_id


@pytest.mark.parametrize("failure_point", ["event", "commit"])
def test_failure_before_commit_reverts_every_change(database, monkeypatch, failure_point):
    service, _ = create(database, count=4)
    # Resolver una semifinal: aceptar la otra crearía la final, que también debe revertirse.
    first, second = service.pending("t1")
    assert service.accept(outcome(issue(service, first)))
    result = outcome(issue(service, second))
    before_events = scalar(database, "SELECT count(*) FROM domain_events")
    original_append = _PostgresEvents.append

    def broken_append(self, event):
        original_append(self, event)
        raise RuntimeError("injected event failure")

    def broken_commit(self):
        raise RuntimeError("injected commit failure")

    with monkeypatch.context() as patch:
        if failure_point == "event":
            patch.setattr(_PostgresEvents, "append", broken_append)
        else:
            patch.setattr(PostgresUnitOfWork, "commit", broken_commit)
        with pytest.raises(RuntimeError, match="injected"):
            service.accept(result)
    assert scalar(database, "SELECT count(*) FROM results") == 1
    assert scalar(database, "SELECT count(*) FROM matches") == 2
    assert scalar(database, "SELECT count(*) FROM domain_events") == before_events
    with PostgresUnitOfWork(database) as work:
        assert work.matches.current_attempt(result.match_id).attempt_id == result.attempt_id
        assert work.matches.accepted(result.match_id) is None
    assert service.accept(result)
    assert scalar(database, "SELECT count(*) FROM matches") == 3


@pytest.mark.parametrize(
    "format_name,count,expected",
    [
        ("elimination", 5, 4),
        ("round_robin", 4, 6),
    ],
)
def test_restart_preserves_both_formats_and_participant_order(
    database, format_name, count, expected
):
    service, spec = create(database, format_name, count)
    played = 0
    while service.pending("t1"):
        match = service.pending("t1")[0]
        assert service.accept(outcome(issue(service, match)))
        played += 1
        service = PersistentTournamentService(factory(database))
    assert played == expected
    with PostgresUnitOfWork(database) as work:
        row = work.tournaments.get("t1")
        assert row["state"] == "completed"
        assert TournamentSpec.from_dict(row["definition"]) == spec
        if format_name == "round_robin":
            assert row["progress"]["standings"] == [[f"c{i}", count - i - 1] for i in range(count)]
        else:
            assert row["progress"]["champion"] == "c0"
        events = work.events.list("t1")
        assert [e.sequence for e in events] == list(range(1, len(events) + 1))
        assert work.events.list("t1", events[-2].sequence) == (events[-1],)


def test_retries_reject_late_generation_and_mismatched_spec(database):
    service, _ = create(database)
    first = issue(service)
    service.invalidate(first.match.match_id, first.attempt_id)
    second = issue(service, first.match, 2)
    assert not service.accept(outcome(first))
    assert not service.accept(replace(outcome(second), generation=1))
    assert not service.accept(replace(outcome(second), winner_id="outsider"))
    assert not service.accept(
        replace(
            outcome(second),
            kind=OutcomeKind.INFRASTRUCTURE_FAILURE,
            winner_id="",
        )
    )
    with pytest.raises(ValueError):
        service.issue(
            replace(second, attempt_id="a3", generation=3, match=replace(second.match, seed=999))
        )
    assert service.accept(outcome(second))
    assert not service.accept(outcome(second))
    with pytest.raises(ValueError):
        issue(service, first.match, 3)


def test_concurrent_initial_attempts_have_one_generation(database):
    service, _ = create(database)
    match = service.pending("t1")[0]

    def try_issue():
        try:
            issue(service, match)
            return True
        except ValueError:
            return False

    assert sorted(race(try_issue, try_issue)) == [False, True]
    assert scalar(database, "SELECT count(*) FROM attempts") == 1


def test_cancel_rejects_current_and_future_attempts(database):
    service, _ = create(database)
    attempt = issue(service)
    service.cancel_tournament("t1")
    service.cancel_tournament("t1")
    assert not service.accept(outcome(attempt))
    with pytest.raises(ValueError):
        issue(service, attempt.match, 2)
    assert not service.pending("t1")
    assert scalar(database, "SELECT count(*) FROM results") == 0


def test_cancel_and_accept_serialize_without_reopening_tournament(database):
    service, _ = create(database, count=4)
    attempt = issue(service)
    accepted, _ = race(
        lambda: service.accept(outcome(attempt)), lambda: service.cancel_tournament("t1")
    )
    assert scalar(database, "SELECT state FROM tournaments") == "cancelled"
    assert scalar(database, "SELECT count(*) FROM results") == int(accepted)
    assert not service.accept(outcome(attempt))
    assert not service.pending("t1")


def test_publish_sees_commit_and_history_survives_publication_failure(database):
    service, _ = create(database, count=2)
    attempt = issue(service)

    def publish(event):
        with PostgresUnitOfWork(database) as reader:
            assert reader.matches.accepted(attempt.match.match_id) == outcome(attempt)
            assert reader.events.list("t1")[-1] == event
        raise OSError("publisher unavailable")

    service.publish = publish
    with pytest.raises(OSError, match="publisher unavailable"):
        service.accept(outcome(attempt))
    assert not service.accept(outcome(attempt))
    assert scalar(database, "SELECT count(*) FROM results") == 1


def test_create_idempotency_conflict_and_failed_creation_leave_no_key(database):
    service, spec = create(database)
    assert service.create(spec, idempotency_key="t1") == "t1"
    with pytest.raises(ValueError, match="idempotencia"):
        service.create(replace(spec, seed=99), idempotency_key="t1")
    broken = replace(spec, tournament_id="broken", artifact_hashes=("f" * 64,) * 4)
    with pytest.raises(psycopg.errors.ForeignKeyViolation):
        service.create(broken, idempotency_key="broken")
    assert scalar(database, "SELECT count(*) FROM idempotency_keys WHERE key = 'broken'") == 0
    assert scalar(database, "SELECT count(*) FROM tournaments WHERE tournament_id = 'broken'") == 0


def test_migration_is_idempotent_and_retains_data(database):
    service, _ = create(database, count=2)
    result = outcome(issue(service))
    assert service.accept(result)
    race(lambda: migrate(database), lambda: migrate(database))
    assert scalar(database, "SELECT count(*) FROM schema_migrations") == 3
    assert PersistentTournamentService(factory(database)).accepted(result.match_id) == result


def test_upgrade_from_initial_schema_preserves_existing_tournament(empty_database):
    schema = (
        files("tournament_management.database").joinpath("migrations/0001_initial.sql").read_text()
    )
    with psycopg.connect(empty_database) as connection:
        connection.execute(schema)
        connection.execute(
            "INSERT INTO tournaments (tournament_id, state) VALUES ('old', 'created')"
        )
    migrate(empty_database)
    assert (
        scalar(empty_database, "SELECT state FROM tournaments WHERE tournament_id = 'old'")
        == "created"
    )
    assert scalar(empty_database, "SELECT count(*) FROM schema_migrations") == 3


def test_concurrent_semifinals_create_only_one_final(database):
    service, _ = create(database)
    first, second = [outcome(issue(service, match)) for match in service.pending("t1")]
    assert race(lambda: service.accept(first), lambda: service.accept(second)) == [True, True]
    assert scalar(database, "SELECT count(*) FROM results") == 2
    assert scalar(database, "SELECT count(*) FROM matches") == 3
    assert len(service.pending("t1")) == 1
    with PostgresUnitOfWork(database) as work:
        events = work.events.list("t1")
        assert [e.sequence for e in events] == list(range(1, len(events) + 1))


def test_concurrent_creations_share_one_resource_and_event(database):
    service, spec = create(database)
    spec = replace(spec, tournament_id="t2")
    assert race(
        lambda: service.create(spec, idempotency_key="t2"),
        lambda: service.create(spec, idempotency_key="t2"),
    ) == ["t2", "t2"]
    assert scalar(database, "SELECT count(*) FROM tournaments WHERE tournament_id = 't2'") == 1
    assert scalar(database, "SELECT count(*) FROM domain_events WHERE tournament_id = 't2'") == 1
    with pytest.raises(ValueError, match="definición"):
        service.create(replace(spec, seed=99), idempotency_key="different-key")
    assert (
        scalar(database, "SELECT count(*) FROM idempotency_keys WHERE key = 'different-key'") == 0
    )


def test_database_enforces_same_match_for_current_attempt(database):
    service, _ = create(database)
    first, second = service.pending("t1")
    attempt = issue(service, first)
    with pytest.raises(psycopg.errors.ForeignKeyViolation), psycopg.connect(database) as connection:
        connection.execute(
            "UPDATE matches SET current_attempt_id = %s WHERE match_id = %s",
            (attempt.attempt_id, second.match_id),
        )


def test_registry_metadata_is_immutable_and_unit_of_work_can_be_reused(database):
    artifact_hash = "f" * 64
    work = PostgresUnitOfWork(database)
    with work:
        work.registry.add_artifact(artifact_hash, 10)
        work.commit()
    # El commit anterior no debe desactivar el rollback del nuevo contexto.
    with work:
        work.registry.add_competitor("c1", "Bot", artifact_hash)
    assert scalar(database, "SELECT count(*) FROM competitors") == 0
    with work:
        work.registry.add_artifact(artifact_hash, 10)
        work.commit()
    with pytest.raises(ValueError, match="metadatos"), work:
        work.registry.add_artifact(artifact_hash, 11)


def test_replacing_queued_attempt_uses_allowed_terminal_state(database):
    service, _ = create(database)
    first = issue(service)
    # Preparar otra partida con intento aún en cola, sin pasar por service.issue.
    match = service.pending("t1")[0]
    queued = replace(first, attempt_id="queued", match=match)
    with PostgresUnitOfWork(database) as work:
        work.matches.add_attempt(queued)
        work.commit()
    replacement = replace(queued, attempt_id="replacement", generation=2)
    service.issue(replacement)
    assert scalar(database, "SELECT state FROM attempts WHERE attempt_id = 'queued'") == "expired"
    assert not service.accept(outcome(queued))
    assert service.accept(outcome(replacement))


def test_failed_migration_rolls_back_ddl_and_version(empty_database, monkeypatch):
    from types import SimpleNamespace

    from tournament_management.database import connection as postgres_connection

    initial = (
        files("tournament_management.database").joinpath("migrations/0001_initial.sql").read_text()
    )

    def resource(filename):
        text = (
            initial
            if filename == "migrations/0001_initial.sql"
            else ("CREATE TABLE partial_change (id INT); SELECT 1 / 0;")
        )
        return SimpleNamespace(read_text=lambda: text)

    monkeypatch.setattr(postgres_connection, "files", lambda _: SimpleNamespace(joinpath=resource))
    with pytest.raises(OSError, match="migrar"):
        migrate(empty_database)
    assert scalar(empty_database, "SELECT to_regclass('partial_change')") is None
    assert scalar(empty_database, "SELECT to_regclass('schema_migrations')") is None
    assert scalar(empty_database, "SELECT to_regclass('tournaments')") is None


def discards(database):
    with PostgresUnitOfWork(database) as work:
        return [
            (e.attempt_id, e.detail)
            for e in work.events.list("t1")
            if e.event_type == "result.discarded"
        ]


def test_every_rejected_result_leaves_a_discard_event_with_its_reason(database):
    service, _ = create(database)
    first = issue(service)
    service.invalidate(first.match.match_id, first.attempt_id)
    second = issue(service, first.match, 2)
    assert not service.accept(outcome(first))
    assert service.accept(outcome(second))
    assert not service.accept(outcome(second))
    assert not service.accept(outcome(first))
    assert discards(database) == [
        (first.attempt_id, "worker=w1 kind=completed reason=stale_attempt"),
        (second.attempt_id, "worker=w1 kind=completed reason=duplicate"),
        (first.attempt_id, "worker=w1 kind=completed reason=stale_attempt"),
    ]
    assert scalar(database, "SELECT count(*) FROM results") == 1


def test_late_failure_and_cancelled_tournament_reasons(database):
    service, _ = create(database)
    attempt = issue(service)
    late_failure = replace(
        outcome(attempt), kind=OutcomeKind.INFRASTRUCTURE_FAILURE, winner_id=""
    )
    assert not service.accept(late_failure)
    service.cancel_tournament("t1")
    assert not service.accept(outcome(attempt))
    assert [detail.rsplit("=", 1)[1] for _, detail in discards(database)] == [
        "not_a_result", "tournament_not_running",
    ]
    assert scalar(database, "SELECT state FROM tournaments") == "cancelled"


def test_dispatched_event_names_the_worker(database):
    service, _ = create(database)
    attempt = issue(service)
    service.dispatched(attempt, "tm-worker-2")
    with PostgresUnitOfWork(database) as work:
        last = work.events.list("t1")[-1]
    assert (last.event_type, last.attempt_id, last.detail) == (
        "attempt.dispatched", attempt.attempt_id, "worker=tm-worker-2",
    )


def test_lab_injection_goes_through_real_acceptance_and_is_discarded(database):
    """4.4 (D4.4 b): la herramienta de laboratorio no puede imponer un resultado viejo."""
    import json
    import subprocess
    import sys
    from pathlib import Path

    service, _ = create(database)
    first = issue(service)
    service.invalidate(first.match.match_id, first.attempt_id)
    second = issue(service, first.match, 2)
    assert service.accept(outcome(second))
    script = Path(__file__).resolve().parents[2] / "experiments/scenarios/inject_late_result.py"
    run = subprocess.run(
        [sys.executable, "-", first.attempt_id], input=script.read_text(encoding="utf-8"),
        env={"PATH": "/usr/bin:/bin", "TM_DATABASE_URL": database},
        capture_output=True, text=True, timeout=30,
    )
    assert run.returncode == 0, run.stderr
    assert json.loads(run.stdout) == {
        "attempt_id": first.attempt_id, "generation": 1, "accepted": False,
    }
    assert discards(database)[-1] == (
        first.attempt_id, "worker=lab-injection kind=completed reason=stale_attempt",
    )
    with PostgresUnitOfWork(database) as work:
        assert work.matches.accepted(first.match.match_id) == outcome(second)


def test_late_result_after_tournament_finished_is_stale_not_cancelled(database):
    service, _ = create(database, count=2)
    first = issue(service)
    service.invalidate(first.match.match_id, first.attempt_id)
    second = issue(service, first.match, 2)
    assert service.accept(outcome(second))
    assert scalar(database, "SELECT state FROM tournaments") == "completed"
    assert not service.accept(outcome(first))
    assert discards(database)[-1][1].endswith("reason=stale_attempt")
