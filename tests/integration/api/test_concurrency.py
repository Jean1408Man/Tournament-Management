"""Varios torneos e intentos simultáneos (5.1), con backend local de N huecos."""

import functools
import itertools
import threading
import time

import pytest

from tournament_management.execution.backends.local import LocalExecution
from tournament_management.shared.messages import AttemptOutcome, OutcomeKind


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


class Slow:
    """Partida que tarda `seconds` y anota cuántas corren a la vez."""

    def __init__(self, seconds):
        self.seconds = seconds
        self.lock = threading.Lock()
        self.running = 0
        self.peak = 0
        self.started: list[str] = []

    def __call__(self, attempt, store, cancel=None):
        with self.lock:
            self.running += 1
            self.peak = max(self.peak, self.running)
            self.started.append(attempt.match.tournament_id)
        try:
            cancel.wait(self.seconds)
            return result(attempt)
        finally:
            with self.lock:
                self.running -= 1


@pytest.fixture
def slots(monkeypatch):
    def use(capacity):
        monkeypatch.setattr(
            "tournament_management.runtime.LocalExecution",
            functools.partial(LocalExecution, capacity=capacity),
        )

    return use


def register(api, *identifiers):
    for identifier in identifiers:
        api.register(identifier)


def test_attempts_run_in_parallel_up_to_worker_capacity(api_factory, slots):
    slots(3)
    runner = Slow(0.4)
    api = api_factory(runner=runner)
    register(api, "a", "b", "c", "d")
    tournament_id = api.create(format_name="round_robin", identifiers=("a", "b", "c", "d"))[2][
        "tournament_id"
    ]
    assert api.terminal(tournament_id)["state"] == "completed"
    assert runner.peak == 3


def test_global_limit_caps_parallel_attempts(api_factory, slots):
    slots(3)
    runner = Slow(0.2)
    api = api_factory(runner=runner, max_running_attempts=2)
    register(api, "a", "b", "c", "d")
    tournament_id = api.create(format_name="round_robin", identifiers=("a", "b", "c", "d"))[2][
        "tournament_id"
    ]
    assert api.terminal(tournament_id)["state"] == "completed"
    assert runner.peak == 2


def test_small_tournament_does_not_wait_for_a_large_one(api_factory, slots):
    slots(2)
    runner = Slow(0.3)
    api = api_factory(runner=runner)
    register(api, "a", "b", "c", "d", "e", "f")
    large = api.create(
        format_name="round_robin", identifiers=("a", "b", "c", "d", "e", "f"), key="large"
    )[2]["tournament_id"]
    time.sleep(0.5)
    small = api.create(identifiers=("a", "b"), key="small")[2]["tournament_id"]
    assert api.terminal(small)["state"] == "completed"
    # 15 partidas de 0,3 s en 2 huecos: el grande sigue en curso cuando acaba el pequeño.
    status, _, data = api.request("GET", f"/api/v1/tournaments/{large}")
    assert (status, data["state"]) == (200, "running")
    assert api.terminal(large)["state"] == "completed"


def test_turns_alternate_between_tournaments(api_factory, slots):
    slots(1)
    runner = Slow(0.05)
    api = api_factory(runner=runner)
    register(api, "a", "b", "c", "d")
    first = api.create(format_name="round_robin", identifiers=("a", "b", "c", "d"), key="x")[2]
    second = api.create(format_name="round_robin", identifiers=("a", "b", "c", "d"), key="y")[2]
    api.terminal(first["tournament_id"])
    api.terminal(second["tournament_id"])
    # Tras arrancar ambos, ninguno encadena más de dos intentos seguidos.
    order = runner.started[runner.started.index(second["tournament_id"]) :]
    runs = [len(list(group)) for _, group in itertools.groupby(order)]
    assert max(runs) <= 2, order


def test_retry_delay_does_not_stop_other_tournaments(api_factory, slots):
    slots(1)
    failed = set()

    def execute(attempt, store, cancel=None):
        if attempt.match.tournament_id == "flaky" and not failed:
            failed.add(attempt.attempt_id)
            return result(attempt, OutcomeKind.INFRASTRUCTURE_FAILURE)
        return result(attempt)

    api = api_factory(runner=execute, retry_delay_ms=3000)
    register(api, "a", "b")
    api.create(identifiers=("a", "b"), key="flaky", tournament_id="flaky")
    time.sleep(0.3)
    started = time.monotonic()
    other = api.create(identifiers=("a", "b"), key="other")[2]["tournament_id"]
    assert api.terminal(other)["state"] == "completed"
    assert time.monotonic() - started < 2
    assert api.terminal("flaky")["state"] == "completed"


def test_cancel_stops_every_attempt_of_that_tournament_only(api_factory, slots):
    slots(4)
    stopped: list[str] = []
    started = threading.Semaphore(0)

    def execute(attempt, store, cancel=None):
        started.release()
        if attempt.match.tournament_id == "victim" and cancel.wait(5):
            stopped.append(attempt.attempt_id)
        else:
            time.sleep(0.2)
        return result(attempt)

    api = api_factory(runner=execute)
    register(api, "a", "b", "c")
    api.create(
        format_name="round_robin", identifiers=("a", "b", "c"), key="v", tournament_id="victim"
    )
    for _ in range(3):
        assert started.acquire(timeout=5)
    other = api.create(identifiers=("a", "b"), key="other")[2]["tournament_id"]
    status, _, data = api.request("POST", "/api/v1/tournaments/victim/cancel")
    assert (status, data["state"]) == (200, "cancelled")
    deadline = time.monotonic() + 5
    while len(stopped) < 3 and time.monotonic() < deadline:
        time.sleep(0.05)
    assert len(stopped) == 3
    assert api.terminal(other)["state"] == "completed"


def test_admission_limit_answers_429_without_persisting_or_consuming_the_key(api_factory):
    release = threading.Event()

    def execute(attempt, store, cancel=None):
        release.wait(10)
        return result(attempt)

    api = api_factory(runner=execute, max_active_tournaments=1)
    register(api, "a", "b")
    assert api.create(key="first", tournament_id="first")[0] == 202
    status, headers, body = api.create(key="second", tournament_id="second")
    assert (status, body["error"]["code"]) == (429, "overloaded")
    assert headers["Retry-After"] == "5"
    assert api.request("GET", "/api/v1/tournaments/second")[0] == 404
    # Repetir la clave de un torneo ya admitido no cuenta como creación nueva.
    assert api.create(key="first", tournament_id="first")[0] == 202
    release.set()
    assert api.terminal("first")["state"] == "completed"
    assert api.create(key="second", tournament_id="second")[0] == 202
    assert api.terminal("second")["state"] == "completed"
