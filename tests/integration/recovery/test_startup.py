"""Recuperación repetible, presupuesto durable y rollback con PostgreSQL real."""

from dataclasses import replace

import pytest

from tournament_management.events.repository import _PostgresEvents
from tournament_management.execution.recovery import StartupRecovery
from tournament_management.shared.messages import OutcomeKind


def test_lost_attempt_is_closed_before_replacement_and_late_result_rejected(scenario_factory):
    scenario = scenario_factory()
    old = scenario.issue()
    recovery = StartupRecovery(scenario.work, max_retries=1)
    assert recovery.recover() == ("t1",)
    assert not scenario.service.accept(scenario.outcome(old))
    with scenario.work() as work:
        match = work.matches.page("t1", offset=0, limit=100)
        recovered = next(item for item in match if item["match_id"] == old.match.match_id)
        assert recovered["state"] == "ready"
        assert recovered["current_attempt_id"] is None
        assert recovered["attempts"][0]["state"] == "lost"
        events = work.events.list("t1")
        assert events[-2].event_type == "attempt.lost"
        assert events[-2].detail == "coordinator_restart"
        # El resultado tardío del intento perdido queda descartado con evidencia.
        assert (events[-1].event_type, events[-1].attempt_id) == (
            "result.discarded", old.attempt_id,
        )
        assert events[-1].detail.endswith("reason=stale_attempt")
    # Otro corte antes de emitir reemplazo no consume una generación ni duplica eventos.
    assert recovery.recover() == ("t1",)
    with scenario.work() as work:
        assert work.events.list("t1") == events
    new = scenario.issue(old.match)
    assert new.generation == 2 and new.attempt_id != old.attempt_id
    assert new.match == old.match
    assert not scenario.service.accept(scenario.outcome(old))
    assert scenario.service.accept(scenario.outcome(new))


def test_each_restart_consumes_existing_budget_instead_of_resetting_it(scenario_factory):
    scenario = scenario_factory(count=2)
    first = scenario.issue()
    recovery = StartupRecovery(scenario.work, max_retries=1)
    assert recovery.recover() == ("t1",)
    second = scenario.issue(first.match)
    assert recovery.recover() == ()
    with scenario.work() as work:
        assert work.tournaments.state("t1").value == "blocked"
        match = work.matches.page("t1", offset=0, limit=100)[0]
        assert match["state"] == "blocked"
        assert [item["state"] for item in match["attempts"]] == ["lost", "lost"]
        event = work.events.list("t1")[-1]
        assert event.detail == "retry_budget_exhausted_on_recovery"
    assert not scenario.service.accept(scenario.outcome(second))
    assert recovery.recover() == ()


def test_failure_committed_before_restart_does_not_create_extra_attempt(scenario_factory):
    scenario = scenario_factory(count=2)
    attempt = scenario.issue()
    failure = replace(
        scenario.outcome(attempt), kind=OutcomeKind.INFRASTRUCTURE_FAILURE, winner_id=""
    )
    assert scenario.service.finish_failure(failure, retry=True)
    # La configuración del siguiente arranque puede ser más restrictiva.
    assert StartupRecovery(scenario.work, max_retries=0).recover() == ()
    with scenario.work() as work:
        assert work.tournaments.state("t1").value == "blocked"
        assert work.matches.next_generation(attempt.match.match_id) == 2


def test_recovery_keeps_committed_results_and_scheduled_next_round(scenario_factory):
    scenario = scenario_factory()
    for match in scenario.service.pending("t1"):
        assert scenario.service.accept(scenario.outcome(scenario.issue(match)))
    pending_before = scenario.service.pending("t1")
    assert len(pending_before) == 1
    with scenario.work() as work:
        before = work.matches.page("t1", offset=0, limit=100)
        events = work.events.list("t1")
    assert StartupRecovery(scenario.work, max_retries=1).recover() == ("t1",)
    assert scenario.service.pending("t1") == pending_before
    with scenario.work() as work:
        assert work.matches.page("t1", offset=0, limit=100) == before
        assert work.events.list("t1") == events


@pytest.mark.parametrize("state", ["completed", "cancelled", "blocked"])
def test_terminal_tournaments_are_never_reactivated(scenario_factory, state):
    scenario = scenario_factory(count=2)
    attempt = scenario.issue()
    if state == "completed":
        scenario.service.accept(scenario.outcome(attempt))
    elif state == "cancelled":
        scenario.service.cancel_tournament("t1")
    else:
        failure = replace(scenario.outcome(attempt), kind=OutcomeKind.NO_WINNER, winner_id="")
        scenario.service.finish_failure(failure, retry=False)
    with scenario.work() as work:
        before = work.matches.page("t1", offset=0, limit=100)
        events = work.events.list("t1")
    assert StartupRecovery(scenario.work, max_retries=5).recover() == ()
    with scenario.work() as work:
        assert work.tournaments.state("t1").value == state
        assert work.matches.page("t1", offset=0, limit=100) == before
        assert work.events.list("t1") == events


def test_recovery_rolls_back_lost_attempt_if_event_cannot_be_committed(
    scenario_factory, monkeypatch
):
    scenario = scenario_factory()
    old = scenario.issue()
    original = _PostgresEvents.append

    def fail(self, event):
        original(self, event)
        raise OSError("injected recovery failure")

    with monkeypatch.context() as patch:
        patch.setattr(_PostgresEvents, "append", fail)
        with pytest.raises(OSError, match="injected"):
            StartupRecovery(scenario.work, max_retries=1).recover()
    with scenario.work() as work:
        assert work.matches.current_attempt(old.match.match_id) == old
        assert [event.event_type for event in work.events.list("t1")] == [
            "tournament.created",
            "attempt.issued",
        ]
    assert StartupRecovery(scenario.work, max_retries=1).recover() == ("t1",)


def test_queue_order_includes_created_without_enqueue_and_skips_legacy(scenario_factory):
    first = scenario_factory(identifier="first")
    scenario_factory(identifier="second")
    with first.work() as work:
        work._connection.execute(
            "INSERT INTO tournaments (tournament_id, state) VALUES ('legacy', 'running')"
        )
        work.commit()
    assert StartupRecovery(first.work, max_retries=1).recover() == ("first", "second")


def test_cancellation_between_scan_and_lock_is_respected(scenario_factory, monkeypatch):
    scenario = scenario_factory(count=2)
    old = scenario.issue()
    with scenario.work() as work:
        repository_type = type(work.tournaments)
    original = repository_type.recoverable_ids

    def cancel_after_scan(self):
        identifiers = original(self)
        scenario.service.cancel_tournament("t1")
        return identifiers

    monkeypatch.setattr(repository_type, "recoverable_ids", cancel_after_scan)
    assert StartupRecovery(scenario.work, max_retries=2).recover() == ()
    assert not scenario.service.accept(scenario.outcome(old))
    with scenario.work() as work:
        assert work.tournaments.state("t1").value == "cancelled"
        assert not any(event.event_type == "attempt.lost" for event in work.events.list("t1"))


def test_second_crash_during_recovery_preserves_committed_first_tournament(
    scenario_factory,
    monkeypatch,
):
    first = scenario_factory(identifier="first")
    second = scenario_factory(identifier="second")
    first_attempt = first.issue(first.service.pending("first")[0])
    second_attempt = second.issue(second.service.pending("second")[0])
    original = _PostgresEvents.append

    def fail_second(self, event):
        original(self, event)
        if event.tournament_id == "second":
            raise OSError("interrupted recovery")

    with monkeypatch.context() as patch:
        patch.setattr(_PostgresEvents, "append", fail_second)
        with pytest.raises(OSError, match="interrupted"):
            StartupRecovery(first.work, max_retries=1).recover()
    with first.work() as work:
        assert work.matches.current_attempt(first_attempt.match.match_id) is None
        assert work.matches.current_attempt(second_attempt.match.match_id) == second_attempt
    assert StartupRecovery(first.work, max_retries=1).recover() == ("first", "second")
    with first.work() as work:
        for identifier in ("first", "second"):
            assert (
                sum(event.event_type == "attempt.lost" for event in work.events.list(identifier))
                == 1
            )
