"""Configuración, diagnóstico y contrato de salud del servicio mínimo."""

import json
import logging
import os
import socket
import subprocess
import sys
import time
from urllib.error import HTTPError
from urllib.request import urlopen

import pytest

from tournament_management.config import Settings
from tournament_management.shared.logging import JsonFormatter


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
        {"TM_DATABASE_URL": "mysql://not-postgresql"},
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


def test_doctor_does_not_expose_database_url(tmp_path):
    result = run_cli(
        "doctor", "--data-dir", str(tmp_path),
        "--database-url", "postgresql://tm:secret@localhost/example",
    )
    assert result.returncode == 0, result.stderr
    assert "secret" not in result.stdout
    assert json.loads(result.stdout)["configuration"]["database_url"] == "[oculta]"


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


def test_health_readiness_and_storage_loss(tmp_path):
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    state = tmp_path / "state"
    env = {key: value for key, value in os.environ.items() if not key.startswith("TM_")}
    process = subprocess.Popen(
        [
            sys.executable, "-m", "tournament_management", "serve",
            "--http-port", str(port), "--data-dir", str(state),
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=env,
    )
    try:
        url = f"http://127.0.0.1:{port}"
        deadline = time.monotonic() + 10
        while True:
            try:
                with urlopen(f"{url}/health", timeout=0.5) as response:
                    assert json.load(response)["status"] == "alive"
                break
            except OSError:
                if process.poll() is not None or time.monotonic() >= deadline:
                    pytest.fail("El servidor no llegó a estar disponible")
                time.sleep(0.05)

        with urlopen(f"{url}/health") as response:
            assert response.status == 200
            assert json.load(response)["status"] == "alive"
        with pytest.raises(HTTPError) as ready_response:
            urlopen(f"{url}/ready")
        assert ready_response.value.code == 503
        state.rmdir()
        with pytest.raises(HTTPError) as unavailable_response:
            urlopen(f"{url}/ready")
        assert unavailable_response.value.code == 503
    finally:
        process.terminate()
        process.communicate(timeout=10)


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
