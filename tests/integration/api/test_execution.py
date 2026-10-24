"""Política durable de intentos y cancelación observada desde HTTP."""

import threading

import pytest

from tournament_management.domain.messages import AttemptOutcome, OutcomeKind


def result(attempt, kind=OutcomeKind.COMPLETED):
    return AttemptOutcome(
        match_id=attempt.match.match_id,
        attempt_id=attempt.attempt_id,
        generation=attempt.generation,
        worker_id="local",
        kind=kind,
        winner_id=attempt.match.competitor_ids[0] if kind == OutcomeKind.COMPLETED else "",
        reason="injected-result",
    )


def test_infrastructure_retry_is_visible_in_match_history(api_factory):
    attempts = []

    def execute(attempt, store, cancel=None):
        attempts.append(attempt)
        return result(
            attempt,
            OutcomeKind.INFRASTRUCTURE_FAILURE if len(attempts) == 1 else OutcomeKind.COMPLETED,
        )

    api = api_factory(runner=execute, max_retries=1)
    api.register("a")
    api.register("b")
    accepted = api.create()[2]
    tournament = api.terminal(accepted["tournament_id"])
    assert tournament["state"] == "completed"
    assert tournament["statistics"]["attempts_total"] == 2
    match = api.request("GET", accepted["status_url"] + "/matches")[2]["items"][0]
    assert [item["state"] for item in match["attempts"]] == ["failed", "accepted"]
    assert [item["generation"] for item in match["attempts"]] == [1, 2]
    assert match["attempts"][0]["outcome"]["kind"] == "infrastructure_failure"
    assert attempts[0].match == attempts[1].match


@pytest.mark.parametrize("kind", [OutcomeKind.INFRASTRUCTURE_FAILURE, OutcomeKind.NO_WINNER])
def test_unresolved_attempt_blocks_without_inventing_winner(api_factory, kind):
    api = api_factory(
        runner=lambda attempt, store, cancel=None: result(attempt, kind), max_retries=0
    )
    api.register("a")
    api.register("b")
    accepted = api.create()[2]
    tournament = api.terminal(accepted["tournament_id"])
    assert tournament["state"] == "blocked"
    assert tournament["champion"] is None
    assert tournament["statistics"]["blocked"] == 1
    match = api.request("GET", accepted["status_url"] + "/matches")[2]["items"][0]
    assert match["winner_id"] is None
    assert match["attempts"][0]["outcome"]["kind"] == kind.value


def test_cancellation_rejects_late_result_and_requests_process_stop(api_factory):
    started = threading.Event()
    stopped = threading.Event()

    def execute(attempt, store, cancel=None):
        started.set()
        if cancel.wait(timeout=5):
            stopped.set()
        return result(attempt)

    api = api_factory(runner=execute)
    api.register("a")
    api.register("b")
    accepted = api.create()[2]
    assert started.wait(timeout=5)
    assert api.request("GET", "/api/v1/workers")[2][0]["running_attempts"] == 1
    status, _, cancelled = api.request("POST", accepted["status_url"] + "/cancel")
    assert status == 200
    assert cancelled["state"] == "cancelled"
    assert stopped.wait(timeout=5)
    assert api.request("POST", accepted["status_url"] + "/cancel")[2] == cancelled
    match = api.request("GET", accepted["status_url"] + "/matches")[2]["items"][0]
    assert match["state"] == "cancelled"
    assert match["winner_id"] is None
    assert match["attempts"][0]["state"] == "cancelled"


def test_attempt_timeout_is_durable_before_replacement(api_factory):
    attempts = []
    first_stopped = threading.Event()

    def execute(attempt, store, cancel=None):
        attempts.append(attempt)
        if attempt.generation == 1:
            if cancel.wait(timeout=5):
                first_stopped.set()
        return result(attempt)

    api = api_factory(
        runner=execute,
        turn_timeout_ms=10,
        match_timeout_ms=20,
        attempt_timeout_ms=100,
        max_retries=1,
    )
    api.register("a")
    api.register("b")
    accepted = api.create()[2]
    assert api.terminal(accepted["tournament_id"])["state"] == "completed"
    assert first_stopped.is_set()
    match = api.request("GET", accepted["status_url"] + "/matches")[2]["items"][0]
    assert match["attempts"][0]["outcome"]["reason"] == "attempt_timeout"
    assert match["accepted_attempt_id"] == attempts[1].attempt_id


def test_database_error_before_accept_does_not_acknowledge_notification(api_factory, monkeypatch):
    api = api_factory(runner=lambda attempt, store, cancel=None: result(attempt))
    runtime = api.app.state.services
    acknowledged = []
    original_acknowledge = runtime.backend.acknowledge

    def acknowledge(attempt_id):
        acknowledged.append(attempt_id)
        original_acknowledge(attempt_id)

    monkeypatch.setattr(runtime.backend, "acknowledge", acknowledge)
    monkeypatch.setattr(
        runtime.tournaments,
        "accept",
        lambda outcome: (_ for _ in ()).throw(
            OSError("injected persistence failure"),
        ),
    )
    api.register("a")
    api.register("b")
    accepted = api.create()[2]
    for _ in range(100):
        if not runtime.coordinator.healthy:
            break
        threading.Event().wait(0.02)
    assert not runtime.coordinator.healthy
    assert not acknowledged
    assert api.request("GET", "/ready")[0] == 503
    assert api.create(key="another")[0] == 503
    match = api.request("GET", accepted["status_url"] + "/matches")[2]["items"][0]
    assert match["winner_id"] is None
