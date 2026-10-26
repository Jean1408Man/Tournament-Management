"""Reinicio limpio, cola pendiente y cancelación con backend que no coopera."""

import threading

import pytest

from tournament_management.execution.backend import ExecutionError
from tournament_management.shared.messages import AttemptOutcome, OutcomeKind


def outcome(attempt):
    return AttemptOutcome(
        match_id=attempt.match.match_id,
        attempt_id=attempt.attempt_id,
        generation=attempt.generation,
        worker_id="local",
        kind=OutcomeKind.COMPLETED,
        winner_id=attempt.match.competitor_ids[0],
        reason="controlled_result",
    )


@pytest.mark.parametrize("format_name", ["elimination", "round_robin"])
def test_shutdown_rebuilds_queue_and_replaces_interrupted_attempt(api_factory, format_name):
    started = threading.Event()
    attempts = []

    def slow(attempt, store, cancel=None):
        attempts.append(attempt)
        started.set()
        assert cancel.wait(timeout=10)
        # Retener la entrega hasta que el hilo coordinador haya terminado:
        # el caso de commit antes del corte se comprueba por separado.
        coordinator._thread.join(timeout=5)
        assert not coordinator._thread.is_alive()
        return outcome(attempt)

    api = api_factory(runner=slow)
    coordinator = api.app.state.services.coordinator
    for identifier in ("a", "b", "c"):
        api.register(identifier)
    first = api.create(format_name=format_name, identifiers=("a", "b", "c"))[2]
    assert started.wait(timeout=5)
    queued = api.create(key="queued", tournament_id="queued")[2]
    assert api.request("GET", queued["status_url"])[2]["statistics"]["attempts_total"] == 0
    api.close()
    resumed = api_factory(runner=lambda attempt, store, cancel=None: outcome(attempt))
    final = resumed.terminal(first["tournament_id"])
    assert final["state"] == "completed"
    assert final["statistics"]["attempts_total"] == (3 if format_name == "elimination" else 4)
    assert resumed.terminal("queued")["state"] == "completed"
    matches = resumed.request("GET", first["status_url"] + "/matches")[2]["items"]
    replaced = next(row for row in matches if row["match_id"] == attempts[0].match.match_id)
    assert [item["state"] for item in replaced["attempts"]] == ["lost", "accepted"]
    assert resumed.create(format_name=format_name, identifiers=("a", "b", "c"))[2] == first


def test_restart_without_retry_budget_blocks_before_submitting_replacement(api_factory):
    started = threading.Event()

    def slow(attempt, store, cancel=None):
        started.set()
        assert cancel.wait(timeout=10)
        coordinator._thread.join(timeout=5)
        assert not coordinator._thread.is_alive()
        return outcome(attempt)

    api = api_factory(runner=slow, max_retries=0)
    coordinator = api.app.state.services.coordinator
    api.register("a")
    api.register("b")
    created = api.create()[2]
    assert started.wait(timeout=5)
    api.close()
    submitted = []

    def replacement(attempt, store, cancel=None):
        submitted.append(attempt)
        return outcome(attempt)

    resumed = api_factory(runner=replacement, max_retries=0)
    state = resumed.terminal(created["tournament_id"])
    assert state["state"] == "blocked"
    assert state["statistics"]["attempts_total"] == 1
    assert not submitted
    assert resumed.request("GET", "/ready")[0] == 200


def test_physical_cancellation_failure_does_not_revoke_confirmed_cancellation(
    api_factory, monkeypatch
):
    started = threading.Event()
    release = threading.Event()
    attempts = []

    def uncooperative(attempt, store, cancel=None):
        attempts.append(attempt)
        started.set()
        release.wait(timeout=10)
        return outcome(attempt)

    api = api_factory(runner=uncooperative)
    api.register("a")
    api.register("b")
    created = api.create()[2]
    assert started.wait(timeout=5)

    def fail_to_stop(attempt_id):
        raise ExecutionError("injected cancellation transport error")

    monkeypatch.setattr(api.app.state.services.backend, "cancel", fail_to_stop)
    try:
        response = api.request("POST", created["status_url"] + "/cancel")
        assert response[0] == 200
        assert response[2]["state"] == "cancelled"
        assert not api.app.state.services.tournaments.accept(outcome(attempts[0]))
        assert api.request("POST", created["status_url"] + "/cancel")[2] == response[2]
    finally:
        release.set()
    api.close()
    resumed = api_factory(runner=lambda attempt, store, cancel=None: outcome(attempt))
    assert resumed.terminal(created["tournament_id"])["state"] == "cancelled"
