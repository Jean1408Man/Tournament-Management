"""Despliegue sin Docker ni descargas: contratos de roles, límites y propiedad."""

import asyncio
import json
import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from tournament_management.adapters.ray_membership import RayConnection, validate_topology
from tournament_management.app import create_app
from tournament_management.application.ports import ExecutionError
from tournament_management.config import Settings

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def fake_ray(monkeypatch):
    module = SimpleNamespace(
        init=Mock(),
        shutdown=Mock(),
        is_initialized=Mock(return_value=False),
        nodes=Mock(
            return_value=[
                {"NodeID": "h", "NodeManagerAddress": "head", "Alive": True, "Resources": {}},
                {
                    "NodeID": "w",
                    "NodeManagerAddress": "worker",
                    "Alive": True,
                    "Resources": {"CPU": 1, "tm_worker": 1},
                },
                {"NodeID": "old", "NodeManagerAddress": "old", "Alive": False, "Resources": {}},
            ]
        ),
    )
    monkeypatch.setitem(sys.modules, "ray", module)
    return module


def test_explicit_client_address_required():
    for address in ("auto", "tm-head:6379", "ray://"):
        with pytest.raises(ValueError):
            RayConnection(address)


def test_connection_and_cleanup(fake_ray):
    connection = RayConnection("ray://tm-head:10001")
    connection.connect()
    nodes = connection.snapshot()
    validate_topology(nodes, 1)
    assert len(nodes) == 2
    fake_ray.init.assert_called_once_with(
        address="ray://tm-head:10001",
        logging_level="ERROR",
        log_to_driver=False,
    )
    connection.close()
    connection.close()
    fake_ray.shutdown.assert_called_once()
    with pytest.raises(ExecutionError):
        connection.snapshot()


def test_failure_is_translated(fake_ray):
    fake_ray.init.side_effect = RuntimeError("middleware error")
    with pytest.raises(ExecutionError):
        RayConnection("ray://tm-head:10001").connect()
    fake_ray.shutdown.assert_called_once()


def test_foreign_connection_is_not_closed(fake_ray):
    fake_ray.is_initialized.return_value = True
    connection = RayConnection("ray://tm-head:10001")
    with pytest.raises(ExecutionError):
        connection.connect()
    connection.close()
    fake_ray.shutdown.assert_not_called()


@pytest.mark.parametrize(
    "nodes",
    [
        [],
        [{"node_id": "h", "cpus": 1, "worker": False}, {"node_id": "w", "cpus": 1, "worker": True}],
        [{"node_id": "h", "cpus": 0, "worker": False}, {"node_id": "w", "cpus": 0, "worker": True}],
    ],
)
def test_wrong_topology_rejected(nodes):
    with pytest.raises(ExecutionError):
        validate_topology(nodes, 1)


def test_app_connects_and_disconnects(fake_ray, tmp_path):
    async def run():
        app = create_app(
            Settings(
                backend="ray",
                ray_address="ray://tm-head:10001",
                data_dir=str(tmp_path),
            )
        )
        async with app.router.lifespan_context(app):
            fake_ray.init.assert_called_once()
            fake_ray.shutdown.assert_not_called()
        fake_ray.shutdown.assert_called_once()

    asyncio.run(run())


def test_startup_failure_does_not_serve(fake_ray, tmp_path):
    fake_ray.init.side_effect = RuntimeError("unavailable")

    async def run():
        app = create_app(
            Settings(
                backend="ray",
                ray_address="ray://tm-head:10001",
                data_dir=str(tmp_path),
            )
        )
        with pytest.raises(ExecutionError):
            async with app.router.lifespan_context(app):
                pytest.fail("No debe servir HTTP si la conexión inicial falla")

    asyncio.run(run())


@pytest.fixture
def fake_commands(tmp_path):
    # Doble de proceso: registra argumentos; no usa daemon ni red.
    program = (
        "#!" + str(Path(sys.executable).resolve()) + "\n"
        "import json, os, sys\n"
        "with open(os.environ['COMMAND_LOG'], 'a') as f:\n"
        "    f.write(json.dumps(sys.argv) + '\\n')\n"
        "if os.path.basename(sys.argv[0]) == 'docker' and 'inspect' in sys.argv:\n"
        "    if not os.environ.get('TEST_OWNED'):\n"
        "        print('foreign-project')\n"
        "    elif any('.Config.Labels' in a for a in sys.argv):\n"
        "        print('tournament-management')\n"
        "    elif '{{.State.Running}}' in sys.argv:\n"
        "        print('true')\n"
        "    elif os.environ.get('TEST_DIFFERENT_IMAGES') and '{{.Image}}' in sys.argv:\n"
        "        print('postgres-image' if 'tm-postgres' in sys.argv else 'app-image')\n"
        "    else:\n"
        "        print('same-image')\n"
        "if 'exec' in sys.argv and 'timeout' in sys.argv:\n"
        "    sys.exit(int(os.environ.get('TEST_MEMBERSHIP_EXIT', '0')))\n"
    )
    for name in ("ray", "tm", "docker"):
        path = tmp_path / name
        path.write_text(program)
        path.chmod(0o755)
    env = {key: value for key, value in os.environ.items() if not key.startswith("TM_")}
    env.update(PATH=str(tmp_path) + ":" + env["PATH"], COMMAND_LOG=str(tmp_path / "calls"))
    return env, tmp_path / "calls"


@pytest.mark.parametrize(
    "role,expected",
    [
        ("ray-head", "--num-cpus=0"),
        ("ray-worker", "--num-cpus=1"),
        ("coordinator", "ray://tm-head:10001"),
    ],
)
def test_entrypoint_roles(fake_commands, role, expected):
    env, calls = fake_commands
    result = subprocess.run(
        ["bash", str(ROOT / "deploy/entrypoint.sh"), role], env=env, capture_output=True, text=True
    )
    assert result.returncode == 0, result.stderr
    args = json.loads(calls.read_text().splitlines()[0])
    assert expected in args
    if role.startswith("ray-"):
        assert "--block" in args
        assert "--num-gpus=0" in args


def test_unknown_role_fails(fake_commands):
    env, calls = fake_commands
    result = subprocess.run(
        ["bash", str(ROOT / "deploy/entrypoint.sh"), "unknown"], env=env, capture_output=True
    )
    assert result.returncode == 2
    assert not calls.exists()


def test_stop_refuses_foreign_container(fake_commands):
    env, calls = fake_commands
    result = subprocess.run(
        ["bash", str(ROOT / "scripts/stop-cluster.sh")], env=env, capture_output=True
    )
    assert result.returncode != 0
    commands = [json.loads(line)[1:] for line in calls.read_text().splitlines()]
    assert not any("stop" in args or "rm" in args for args in commands)


def test_fail_worker_refuses_non_worker(fake_commands):
    env, calls = fake_commands
    result = subprocess.run(
        ["bash", str(ROOT / "scripts/fail-worker.sh"), "tm-head"], env=env, capture_output=True
    )
    assert result.returncode != 0
    assert not calls.exists()


def test_membership_timeout_is_reported_and_runs_inside_container(fake_commands):
    env, calls = fake_commands
    env.update(TEST_OWNED="1", TEST_MEMBERSHIP_EXIT="124")
    result = subprocess.run(
        ["bash", str(ROOT / "scripts/cluster-status.sh")],
        env=env,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 124
    assert "124 indica timeout" in result.stderr
    commands = [json.loads(line)[1:] for line in calls.read_text().splitlines()]
    membership = next(args for args in commands if "timeout" in args)
    assert membership[:4] == ["exec", "tm-coordinator", "timeout", "--kill-after=2"]
    assert any("/tmp/ray/session_latest/logs/ray_client_server.err" in a for a in commands)
    assert not any("doctor" in args for args in commands)


def test_status_uses_ray_configuration_for_doctor(fake_commands):
    env, calls = fake_commands
    env.update(TEST_OWNED="1", TEST_DIFFERENT_IMAGES="1")
    result = subprocess.run(
        ["bash", str(ROOT / "scripts/cluster-status.sh")],
        env=env,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    commands = [json.loads(line)[1:] for line in calls.read_text().splitlines()]
    doctor = next(args for args in commands if "doctor" in args)
    assert doctor[-4:] == ["--backend", "ray", "--http-host", "0.0.0.0"]


def test_dotenv_read_as_data_and_environment_wins(fake_commands, tmp_path):
    env, _ = fake_commands
    config = tmp_path / "settings.env"
    marker = tmp_path / "executed"
    config.write_text(
        "TM_API_PORT=8765\n"
        "TM_POSTGRES_PASSWORD=fromfile\n"
        f"IGNORED=$(touch {marker})\n"
    )
    env.update(TM_ENV_FILE=str(config), TM_POSTGRES_PASSWORD="fromenvironment")
    result = subprocess.run(
        [
            "bash",
            "-c",
            'source "$1"; printf "%s %s" "$TM_API_PORT" "$TM_POSTGRES_PASSWORD"',
            "test",
            str(ROOT / "scripts/common.sh"),
        ],
        env=env,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout == "8765 fromenvironment"
    assert not marker.exists()


def test_stop_uses_timeout_and_preserves_volume(fake_commands):
    env, calls = fake_commands
    env["TEST_OWNED"] = "1"
    result = subprocess.run(
        ["bash", str(ROOT / "scripts/stop-cluster.sh")],
        env=env,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    commands = [json.loads(line)[1:] for line in calls.read_text().splitlines()]
    stops = [args for args in commands if args[0] == "stop"]
    assert len(stops) == 5
    assert all(args[1:3] == ["--timeout", "20"] for args in stops)
    assert not any(args[0] in {"volume", "network"} for args in commands)
