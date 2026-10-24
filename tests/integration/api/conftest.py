"""Cliente HTTP real y servidor local, sin dependencias de prueba adicionales."""

import json
import socket
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen

import pytest
import uvicorn
from psycopg.conninfo import conninfo_to_dict

from tournament_management.app import create_app
from tournament_management.config import Settings

ROOT = Path(__file__).resolve().parents[3]


@dataclass
class ApiClient:
    url: str
    app: object

    def request(self, method, path, *, body=None, data=None, headers=None):
        headers = dict(headers or {})
        if body is not None:
            data = json.dumps(body).encode()
            headers["Content-Type"] = "application/json"
        request = Request(self.url + path, data=data, headers=headers, method=method)
        try:
            response = urlopen(request, timeout=10)
        except HTTPError as error:
            response = error
        with response:
            payload = response.read()
            return response.status, response.headers, json.loads(payload) if payload else None

    def register(self, identifier, source=None, key=None):
        if source is None:
            source = (ROOT / "bots" / "optimal.py").read_bytes()
        query = urlencode(
            {"name": identifier, "filename": "new-bot.py", "competitor_id": identifier}
        )
        return self.request(
            "POST",
            "/api/v1/competitors?" + query,
            data=source,
            headers={
                "Content-Type": "application/octet-stream",
                "Idempotency-Key": key or identifier,
            },
        )

    def create(self, *, format_name="elimination", identifiers=("a", "b"), key="t1", **fields):
        return self.request(
            "POST",
            "/api/v1/tournaments",
            body={
                "format": format_name,
                "competitor_ids": list(identifiers),
                **fields,
            },
            headers={"Idempotency-Key": key},
        )

    def terminal(self, tournament_id):
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            status, _, data = self.request("GET", f"/api/v1/tournaments/{tournament_id}")
            assert status == 200, data
            if data["state"] in {"completed", "blocked", "cancelled"}:
                return data
            time.sleep(0.05)
        pytest.fail("El torneo no alcanzó un estado terminal")

    def sse(self, tournament_id, *, last=None, after=None):
        path = f"/api/v1/tournaments/{tournament_id}/events"
        if after is not None:
            path += f"?after={after}"
        headers = {"Last-Event-ID": last} if last is not None else {}
        return urlopen(Request(self.url + path, headers=headers), timeout=10)


@pytest.fixture
def api_factory(database, tmp_path, monkeypatch):
    running = []

    def start(*, runner=None, **settings):
        if runner is not None:
            monkeypatch.setattr("tournament_management.runtime.run_registered_match", runner)
        configuration = {
            "database_url": "postgresql:///?"
            + urlencode(conninfo_to_dict(database), quote_via=quote),
            "data_dir": str(tmp_path / "state"),
            "retry_delay_ms": 0,
            **settings,
        }
        app = create_app(Settings(**configuration))
        sock = socket.socket()
        sock.bind(("127.0.0.1", 0))
        sock.listen(128)
        server = uvicorn.Server(
            uvicorn.Config(
                app,
                ws="none",
                log_level="error",
                timeout_graceful_shutdown=2,
            )
        )
        thread = threading.Thread(target=server.run, kwargs={"sockets": [sock]}, daemon=True)
        thread.start()
        running.append((server, thread, sock))
        deadline = time.monotonic() + 10
        while not server.started:
            if not thread.is_alive() or time.monotonic() > deadline:
                pytest.fail("No arrancó la API de prueba")
            time.sleep(0.02)
        return ApiClient(f"http://127.0.0.1:{sock.getsockname()[1]}", app)

    yield start
    for server, thread, sock in reversed(running):
        server.should_exit = True
        thread.join(timeout=10)
        sock.close()
        assert not thread.is_alive(), "El servidor o el coordinador no se cerró limpiamente"


@pytest.fixture
def api(api_factory):
    return api_factory()
