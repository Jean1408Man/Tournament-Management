"""Configuración, diagnóstico y contrato de salud del servicio mínimo."""

import asyncio
import json
import logging
import os
import socket
import subprocess
import sys
import time
from urllib.request import urlopen

import pytest

from tournament_management.app import create_app
from tournament_management.config import Settings
from tournament_management.observability.logging import JsonFormatter


def test_precedence():
    settings = Settings.load({"http_port": "9000"}, {"TM_HTTP_PORT": "7000", "TM_BACKEND": "ray"})
    assert settings.http_port == 9000
    assert settings.backend == "ray"
    assert settings.role == "coordinator"


@pytest.mark.parametrize(
    "env",
    [
        {"TM_HTTP_PORT": "wrong"},
        {"TM_HTTP_PORT": "65536"},
        {"TM_BACKEND": "unknown"},
        {"TM_ROLE": "unknown"},
        {"TM_MAX_RETRIES": "-1"},
        {"TM_LOG_LEVEL": "invalid"},
        {"TM_DATA_DIR": ""},
        {"TM_TURN_TIMEOUT_MS": "90001"},
        {"TM_MEMORY_LIMIT_BYTES": "0"},
    ],
)
def test_invalid_configuration(env):
    with pytest.raises(ValueError):
        Settings.load({}, env)


def test_cli_override_ignores_invalid_lower_priority_value():
    assert Settings.load({"http_port": "9000"}, {"TM_HTTP_PORT": "bad"}).http_port == 9000


def run_cli(*args):
    env = {key: value for key, value in os.environ.items() if not key.startswith("TM_")}
    return subprocess.run(
        [sys.executable, "-m", "tournament_management", *args],
        capture_output=True,
        text=True,
        env=env,
    )


def test_doctor(tmp_path):
    result = run_cli("doctor", "--data-dir", str(tmp_path / "state"))
    assert result.returncode == 0, result.stderr
    report = json.loads(result.stdout)
    assert report["status"] == "ok"
    assert report["cluster_checked"] is False
    assert list((tmp_path / "state").iterdir()) == []


def test_doctor_unwritable_target(tmp_path):
    target = tmp_path / "file"
    target.write_text("existing")
    result = run_cli("doctor", "--data-dir", str(target))
    assert result.returncode == 1
    assert json.loads(result.stdout)["status"] == "error"
    assert target.read_text() == "existing"


@pytest.mark.parametrize(
    "args",
    [
        ("probe-cluster",),
        ("match",),
        ("simulate",),
        ("verify",),
        ("serve", "--http-port", "wrong"),
        ("serve", "--role", "ray-worker"),
    ],
)
def test_cli_rejects_unsupported_actions(args):
    result = run_cli(*args)
    assert result.returncode == 2
    assert result.stderr
    assert result.stdout == ""


async def request(app, path):
    responses = []

    async def receive():
        return {"type": "http.request", "body": b""}

    async def send(message):
        responses.append(message)

    await app(
        {
            "type": "http",
            "asgi": {"version": "3.0"},
            "http_version": "1.1",
            "method": "GET",
            "scheme": "http",
            "path": path,
            "raw_path": path.encode(),
            "query_string": b"",
            "root_path": "",
            "headers": [],
            "client": ("127.0.0.1", 1234),
            "server": ("127.0.0.1", 8000),
        },
        receive,
        send,
    )
    return responses[0]["status"], json.loads(responses[1]["body"])


def test_health_readiness_and_storage_loss(tmp_path):
    async def scenario():
        app = create_app(Settings(data_dir=str(tmp_path / "state")))
        async with app.router.lifespan_context(app):
            status, payload = await request(app, "/health")
            assert status == 200
            assert payload["status"] == "alive"
            status, payload = await request(app, "/ready")
            assert status == 503
            assert payload["checks"] == {"storage": "ready", "execution": "not_implemented"}
            (tmp_path / "state").rmdir()
            status, payload = await request(app, "/ready")
            assert status == 503
            assert payload["checks"]["storage"] == "unavailable"

    asyncio.run(scenario())


def test_json_logs():
    record = logging.LogRecord("test", logging.INFO, __file__, 1, "message %s", ("hello",), None)
    record.attempt_id = "attempt-1"
    payload = json.loads(JsonFormatter().format(record))
    assert payload["message"] == "message hello"
    assert payload["attempt_id"] == "attempt-1"
    assert payload["timestamp"].endswith("+00:00")
    assert isinstance(payload["monotonic_ns"], int)


def test_real_server_shutdown(tmp_path):
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    env = {key: value for key, value in os.environ.items() if not key.startswith("TM_")}
    process = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "tournament_management",
            "serve",
            "--http-port",
            str(port),
            "--data-dir",
            str(tmp_path / "server"),
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=env,
    )
    try:
        deadline = time.monotonic() + 10
        while True:
            try:
                with urlopen(f"http://127.0.0.1:{port}/health", timeout=0.5) as response:
                    assert json.load(response)["status"] == "alive"
                break
            except OSError:
                if process.poll() is not None or time.monotonic() >= deadline:
                    pytest.fail("El servidor no llegó a estar disponible")
                time.sleep(0.05)
        process.terminate()
        stdout, stderr = process.communicate(timeout=10)
        assert stdout == ""
        logs = [json.loads(line) for line in stderr.splitlines()]
        assert any(log["message"] == "Servicio detenido limpiamente" for log in logs)
    finally:
        if process.poll() is None:
            process.kill()
            process.communicate(timeout=5)
