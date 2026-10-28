"""Sin capacidad de workers no se emiten intentos ni se gasta presupuesto (4.3, D3.3)."""

import json
import threading
import time

import pytest

from tournament_management.execution.backends.local import LocalExecution
from tournament_management.shared.messages import WorkerInfo, WorkerStatus

FULL = (WorkerInfo(worker_id="local", status=WorkerStatus.AVAILABLE, capacity=1,
                   running_attempts=1),)


@pytest.fixture
def capacity(monkeypatch):
    """Mientras el evento no esté activo, el único worker figura ocupado."""
    available = threading.Event()
    original = LocalExecution.workers
    monkeypatch.setattr(
        LocalExecution, "workers",
        lambda self: original(self) if available.is_set() else FULL,
    )
    return available


def attempts(api, tournament_id):
    page = api.request("GET", f"/api/v1/tournaments/{tournament_id}/matches")[2]
    return sum(len(match["attempts"]) for match in page["items"])


def events(api, tournament_id):
    with api.sse(tournament_id) as stream:
        lines = stream.read().decode().splitlines()
    return [json.loads(line[5:]) for line in lines if line.startswith("data:")]


def setup(api):
    api.register("a")
    api.register("b")
    return api.create()[2]["tournament_id"]


def test_waits_without_attempts_and_continues_when_capacity_returns(api_factory, capacity):
    api = api_factory(capacity_wait_ms=60_000)
    tournament_id = setup(api)
    time.sleep(1.5)
    status, _, data = api.request("GET", f"/api/v1/tournaments/{tournament_id}")
    assert (status, data["state"]) == (200, "running")
    assert attempts(api, tournament_id) == 0
    capacity.set()
    assert api.terminal(tournament_id)["state"] == "completed"
    # Un solo intento por partida: esperar no consumió generaciones.
    assert attempts(api, tournament_id) == 1


def test_wait_limit_blocks_with_explicit_reason(api_factory, capacity):
    api = api_factory(capacity_wait_ms=300)
    tournament_id = setup(api)
    assert api.terminal(tournament_id)["state"] == "blocked"
    assert attempts(api, tournament_id) == 0
    blocked = [e for e in events(api, tournament_id) if e["event_type"] == "tournament.blocked"]
    assert [e["detail"] for e in blocked] == ["no_worker_capacity"]
    assert api.request("GET", "/ready")[0] == 200


def test_cancel_while_waiting_emits_no_attempts(api_factory, capacity):
    api = api_factory(capacity_wait_ms=60_000)
    tournament_id = setup(api)
    time.sleep(0.5)
    status, _, data = api.request("POST", f"/api/v1/tournaments/{tournament_id}/cancel")
    assert (status, data["state"]) == (200, "cancelled")
    time.sleep(1)
    capacity.set()
    time.sleep(0.5)
    assert attempts(api, tournament_id) == 0
    assert api.request("GET", "/ready")[0] == 200
