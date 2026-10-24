"""Cortes SIGKILL reales y reapertura de la misma base/directorio por HTTP."""

import json
import socket
import subprocess
import sys
import time
from pathlib import Path
from urllib.parse import quote, urlencode

import pytest
from psycopg.conninfo import conninfo_to_dict

from tournament_management.adapters.postgres_store import PostgresUnitOfWork
from tournament_management.domain.messages import AttemptOutcome

from .client import ApiClient


@pytest.fixture
def crash_server(database, tmp_path):
    processes = []

    def start(phase):
        configuration = tmp_path / "crash-settings.json"
        configuration.write_text(
            json.dumps(
                {
                    "database_url": "postgresql:///?"
                    + urlencode(conninfo_to_dict(database), quote_via=quote),
                    "data_dir": str(tmp_path / "state"),
                    "retry_delay_ms": 0,
                }
            )
        )
        marker = tmp_path / "commit-marker.json"
        listener = socket.socket()
        listener.bind(("127.0.0.1", 0))
        listener.listen(128)
        url = f"http://127.0.0.1:{listener.getsockname()[1]}"
        log = (tmp_path / "crash-server.log").open("w")
        try:
            process = subprocess.Popen(
                [
                    sys.executable,
                    str(Path(__file__).with_name("recovery_server.py")),
                    str(configuration),
                    str(marker),
                    phase,
                    str(listener.fileno()),
                ],
                pass_fds=(listener.fileno(),),
                stdout=log,
                stderr=subprocess.STDOUT,
            )
        finally:
            listener.close()
            log.close()
        processes.append(process)
        api = ApiClient(url)
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            if process.poll() is not None:
                pytest.fail((tmp_path / "crash-server.log").read_text())
            try:
                if api.request("GET", "/ready")[0] == 200:
                    return api, process, marker
            except OSError:
                pass
            time.sleep(0.02)
        pytest.fail("El proceso HTTP no arrancó")

    yield start
    for process in processes:
        if process.poll() is None:
            process.kill()
        process.wait(timeout=10)


def await_marker(marker, process):
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        assert process.poll() is None
        if marker.exists():
            return AttemptOutcome.from_json(marker.read_text())
        time.sleep(0.02)
    pytest.fail("No se alcanzó el límite de commit instrumentado")


@pytest.mark.parametrize("format_name,played", [("elimination", 3), ("round_robin", 6)])
@pytest.mark.parametrize("phase", ["before", "after"])
def test_sigkill_around_accept_recovers_without_losing_or_repeating_results(
    crash_server,
    api_factory,
    database,
    format_name,
    played,
    phase,
):
    api, process, marker = crash_server(phase)
    identifiers = ("a", "b", "c", "d")
    for identifier in identifiers:
        assert api.register(identifier)[0] == 201
    created = api.create(format_name=format_name, identifiers=identifiers)[2]
    old = await_marker(marker, process)
    before = api.request("GET", created["status_url"] + "/matches")[2]["items"]
    old_row = next(row for row in before if row["match_id"] == old.match_id)
    assert (old_row["state"] == "completed") == (phase == "after")
    with PostgresUnitOfWork(database) as work:
        confirmed = work.events.list(created["tournament_id"])
    process.kill()
    process.wait(timeout=10)

    restarted = api_factory()
    assert restarted.request("GET", "/ready")[0] == 200
    final = restarted.terminal(created["tournament_id"])
    assert final["state"] == "completed"
    assert final["statistics"]["completed"] == played
    assert final["statistics"]["attempts_total"] == played + (phase == "before")
    rows = restarted.request("GET", created["status_url"] + "/matches")[2]["items"]
    row = next(item for item in rows if item["match_id"] == old.match_id)
    assert row["spec"] == old_row["spec"]
    if phase == "after":
        assert row == old_row
    else:
        assert [item["state"] for item in row["attempts"]] == ["lost", "accepted"]
        assert [item["generation"] for item in row["attempts"]] == [1, 2]
        assert row["accepted_attempt_id"] != old.attempt_id
    assert not restarted.app.state.services.tournaments.accept(old)
    # El cliente puede reconectar con un ID confirmado antes del corte.
    last = confirmed[-1].sequence
    with restarted.sse(
        created["tournament_id"], last=f"{created['tournament_id']}:{last}"
    ) as stream:
        ids = [
            int(line.rsplit(":", 1)[1])
            for line in stream.read().decode().splitlines()
            if line.startswith("id: ")
        ]
    assert ids == list(range(last + 1, last + 1 + len(ids)))
    assert ids
    assert restarted.create(format_name=format_name, identifiers=identifiers)[2] == created


def test_cancelled_tournament_does_not_reactivate_after_sigkill(crash_server, api_factory):
    api, process, marker = crash_server("before")
    api.register("a")
    api.register("b")
    created = api.create()[2]
    old = await_marker(marker, process)
    cancelled = api.request("POST", created["status_url"] + "/cancel")[2]
    assert cancelled["state"] == "cancelled"
    before = api.request("GET", created["status_url"] + "/matches")[2]
    process.kill()
    process.wait(timeout=10)
    restarted = api_factory()
    assert restarted.terminal(created["tournament_id"])["state"] == "cancelled"
    assert restarted.request("GET", created["status_url"] + "/matches")[2] == before
    assert not restarted.app.state.services.tournaments.accept(old)
    assert restarted.create()[2] == created
    assert restarted.request("POST", created["status_url"] + "/cancel")[2] == cancelled
