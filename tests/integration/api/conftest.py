"""Cliente HTTP real y servidor local, sin dependencias de prueba adicionales."""

import socket
import threading
import time
from urllib.parse import quote, urlencode

import pytest
import uvicorn
from psycopg.conninfo import conninfo_to_dict

from tournament_management.app import create_app
from tournament_management.config import Settings

from .client import ApiClient


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

        def stop():
            server.should_exit = True
            thread.join(timeout=10)
            sock.close()
            assert not thread.is_alive(), "El servidor o el coordinador no se cerró limpiamente"

        running.append(stop)
        deadline = time.monotonic() + 10
        while not server.started:
            if not thread.is_alive() or time.monotonic() > deadline:
                pytest.fail("No arrancó la API de prueba")
            time.sleep(0.02)
        return ApiClient(f"http://127.0.0.1:{sock.getsockname()[1]}", app, stop)

    yield start
    for stop in reversed(running):
        stop()


@pytest.fixture
def api(api_factory):
    return api_factory()
