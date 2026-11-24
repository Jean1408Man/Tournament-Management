"""Despliegue sin Docker ni descargas: contratos de roles, límites y propiedad."""

import asyncio
import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from tournament_management.app import create_app
from tournament_management.cluster.ray_connection import RayConnection
from tournament_management.cluster.ray_topology import validate_topology
from tournament_management.config import Settings
from tournament_management.execution.backend import ExecutionError

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
        "    if sys.argv[-1] in os.environ.get('TEST_MISSING', '').split(','):\n"
        "        sys.exit(1)\n"
        "    if any('.Config.Env' in a for a in sys.argv):\n"
        "        print('PATH=/usr/bin')\n"
        "        print('TM_INTERNAL_TOKEN=' + os.environ.get('TEST_TOKEN', ''))\n"
        "        sys.exit(0)\n"
        "    if os.environ.get('TEST_START') and sys.argv[1:3] == ['container', 'inspect']:\n"
        "        calls = [json.loads(line) for line in open(os.environ['COMMAND_LOG'])]\n"
        "        created = [c[c.index('--name')+1] for c in calls if '--name' in c]\n"
        "        created += os.environ.get('TEST_PRESENT', '').split(',')\n"
        "        if sys.argv[-1] not in created:\n"
        "            sys.exit(1)\n"
        "    if not os.environ.get('TEST_OWNED'):\n"
        "        print('foreign-project')\n"
        "    elif any('.Labels' in a for a in sys.argv):\n"
        "        print('tournament-management')\n"
        "    elif '{{.Driver}}' in sys.argv:\n"
        "        print('bridge')\n"
        "    elif any('.Mounts' in a for a in sys.argv):\n"
        "        print(os.environ.get('TEST_MOUNTED', ''))\n"
        "    elif '{{.State.Running}}' in sys.argv:\n"
        "        print('true')\n"
        "    elif os.environ.get('TEST_DIFFERENT_IMAGES') and '{{.Image}}' in sys.argv:\n"
        "        print('coordinator-image' if 'tm-coordinator' in sys.argv else 'app-image')\n"
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
        ["bash", str(ROOT / "deploy/docker/entrypoint.sh"), role],
        env=env, capture_output=True, text=True,
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
        ["bash", str(ROOT / "deploy/docker/entrypoint.sh"), "unknown"], env=env, capture_output=True
    )
    assert result.returncode == 2
    assert not calls.exists()


def test_stop_refuses_foreign_container(fake_commands):
    env, calls = fake_commands
    result = subprocess.run(
        ["bash", str(ROOT / "deploy/docker/stop-cluster.sh")], env=env, capture_output=True
    )
    assert result.returncode != 0
    commands = [json.loads(line)[1:] for line in calls.read_text().splitlines()]
    assert not any("stop" in args or "rm" in args for args in commands)


def test_fail_worker_refuses_non_worker(fake_commands):
    env, calls = fake_commands
    result = subprocess.run(
        ["bash", str(ROOT / "deploy/docker/fail-worker.sh"), "tm-head"],
        env=env, capture_output=True,
    )
    assert result.returncode != 0
    assert not calls.exists()


def test_membership_timeout_is_reported_and_runs_inside_container(fake_commands):
    env, calls = fake_commands
    env.update(TEST_OWNED="1", TEST_MEMBERSHIP_EXIT="124")
    result = subprocess.run(
        ["bash", str(ROOT / "deploy/docker/cluster-status.sh")],
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
        ["bash", str(ROOT / "deploy/docker/cluster-status.sh")],
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
    config.write_text(f"TM_API_PORT=8765\nTM_BACKEND=fromfile\nIGNORED=$(touch {marker})\n")
    env.update(TM_ENV_FILE=str(config), TM_BACKEND="fromenvironment")
    result = subprocess.run(
        [
            "bash",
            "-c",
            'source "$1"; printf "%s %s" "$TM_API_PORT" "$TM_BACKEND"',
            "test",
            str(ROOT / "deploy/docker/common.sh"),
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
        ["bash", str(ROOT / "deploy/docker/stop-cluster.sh")],
        env=env,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    commands = [json.loads(line)[1:] for line in calls.read_text().splitlines()]
    stops = [args for args in commands if args[0] == "stop"]
    assert [args[-1] for args in stops] == [
        "tm-coordinator", "tm-worker-4", "tm-worker-3", "tm-worker-2", "tm-worker-1",
        "tm-head", "tm-postgres",
    ]
    assert all(args[1:3] == ["--timeout", "20"] for args in stops)
    assert not any(args[0] in {"volume", "network"} for args in commands)


def start_runs(fake_commands, **extra):
    env, calls = fake_commands
    env.update(TEST_OWNED="1", TEST_START="1", **extra)
    result = subprocess.run(
        ["bash", str(ROOT / "deploy/docker/start-cluster.sh")],
        env=env,
        capture_output=True,
        text=True,
        timeout=20,
    )
    assert result.returncode == 0, result.stderr
    commands = [json.loads(line)[1:] for line in calls.read_text().splitlines()]
    return {args[args.index("--name") + 1]: args for args in commands if args[0] == "run"}


def env_value(args, name):
    values = [arg.split("=", 1)[1] for arg in args if arg.startswith(f"{name}=")]
    return values[0] if values else None


def test_start_gives_workers_download_access_without_shared_volume(fake_commands):
    runs = start_runs(fake_commands)
    token = env_value(runs["tm-coordinator"], "TM_INTERNAL_TOKEN")
    assert token and len(token) >= 16
    workers = [name for name in runs if name.startswith("tm-worker-")]
    assert workers == ["tm-worker-1", "tm-worker-2", "tm-worker-3", "tm-worker-4"]
    for worker in workers:
        assert runs[worker][runs[worker].index("--memory") + 1] == "2g"
        assert not any("tm-artifacts" in arg for arg in runs[worker])
        assert not any("source=tm-state" in arg for arg in runs[worker])
        assert env_value(runs[worker], "TM_DATABASE_URL") is None
        assert env_value(runs[worker], "TM_ARTIFACT_URL") == "http://tm-coordinator:8000"
        assert env_value(runs[worker], "TM_INTERNAL_TOKEN") == token
    assert "source=tm-artifacts,target=/var/lib/tm/artifacts" in runs["tm-coordinator"]
    assert not any("tm-artifacts" in arg for arg in runs["tm-head"])
    assert env_value(runs["tm-head"], "TM_INTERNAL_TOKEN") is None


def test_start_runs_the_database_inside_the_coordinator(fake_commands):
    runs = start_runs(fake_commands)
    assert "tm-postgres" not in runs
    coordinator = runs["tm-coordinator"]
    assert coordinator[coordinator.index("--cpus") + 1] == "2"
    assert coordinator[coordinator.index("--memory") + 1] == "2g"
    assert "source=tm-postgres-data,target=/var/lib/postgresql/data" in coordinator
    # La URL de la base la fija la imagen del coordinador: no viaja en el arranque.
    assert env_value(coordinator, "TM_DATABASE_URL") is None
    for name in runs.keys() - {"tm-coordinator"}:
        assert not any("tm-postgres-data" in arg for arg in runs[name])
    commands = [json.loads(line)[1:] for line in fake_commands[1].read_text().splitlines()]
    images = [args[-1] for args in commands if args[:2] == ["image", "inspect"]]
    assert images[:2] == [
        "tournament-management:step-1.5", "tournament-management:step-1.5-coordinator"
    ]


def test_start_refuses_while_the_old_database_container_exists(fake_commands):
    env, calls = fake_commands
    env.update(TEST_OWNED="1", TEST_START="1", TEST_PRESENT="tm-postgres")
    result = subprocess.run(
        ["bash", str(ROOT / "deploy/docker/start-cluster.sh")],
        env=env, capture_output=True, text=True, timeout=20,
    )
    assert result.returncode != 0
    assert "tm-postgres" in result.stderr
    commands = [json.loads(line)[1:] for line in calls.read_text().splitlines()]
    assert not any(args[0] in {"run", "volume", "network"} for args in commands)


def test_build_creates_worker_and_coordinator_images(fake_commands):
    env, calls = fake_commands
    result = subprocess.run(
        ["bash", str(ROOT / "deploy/docker/build-image.sh")],
        env=env, capture_output=True, text=True, timeout=20,
    )
    assert result.returncode == 0, result.stderr
    builds = [json.loads(line)[1:] for line in calls.read_text().splitlines()]
    assert [
        (args[args.index("--target") + 1], args[args.index("--tag") + 1])
        for args in builds
        if args[0] == "build"
    ] == [
        ("worker", "tournament-management:step-1.5"),
        ("coordinator", "tournament-management:step-1.5-coordinator"),
    ]


def test_only_the_coordinator_image_installs_postgresql():
    text = (ROOT / "deploy/docker/Dockerfile").read_text(encoding="utf-8")
    stages = {}
    for block in text.split("\nFROM ")[1:]:
        header, _, body = block.partition("\n")
        stages[header.split(" AS ")[1].strip()] = body
    assert stages.keys() == {"base", "worker", "coordinator"}
    assert '"postgresql-$PG_MAJOR"' in stages["coordinator"]
    assert "postgresql" not in stages["base"].lower()
    assert "postgresql" not in stages["worker"].lower()


def test_worker_count_and_memory_are_configured_in_one_place(fake_commands):
    runs = start_runs(fake_commands, TM_WORKERS="2", TM_WORKER_MEMORY="1536m")
    workers = [name for name in runs if name.startswith("tm-worker-")]
    assert workers == ["tm-worker-1", "tm-worker-2"]
    assert all(runs[w][runs[w].index("--memory") + 1] == "1536m" for w in workers)
    assert runs["tm-head"][runs["tm-head"].index("--memory") + 1] == "2g"


@pytest.mark.parametrize("value", ["0", "dos", "-1"])
def test_invalid_worker_count_is_rejected(fake_commands, value):
    env, calls = fake_commands
    env.update(TEST_OWNED="1", TM_WORKERS=value)
    result = subprocess.run(
        ["bash", str(ROOT / "deploy/docker/start-cluster.sh")],
        env=env, capture_output=True, text=True,
    )
    assert result.returncode != 0
    assert "TM_WORKERS" in result.stderr
    assert not calls.exists()


def test_start_generates_a_new_token_unless_exported(fake_commands, tmp_path):
    first = env_value(start_runs(fake_commands)["tm-coordinator"], "TM_INTERNAL_TOKEN")
    (tmp_path / "calls").unlink()
    second = env_value(start_runs(fake_commands)["tm-coordinator"], "TM_INTERNAL_TOKEN")
    assert first != second
    (tmp_path / "calls").unlink()
    fixed = start_runs(fake_commands, TM_INTERNAL_TOKEN="a" * 32)["tm-worker-1"]
    assert env_value(fixed, "TM_INTERNAL_TOKEN") == "a" * 32


def add_worker(fake_commands, name, **extra):
    env, calls = fake_commands
    env.update(TEST_OWNED="1", TEST_MISSING="tm-worker-3", **extra)
    result = subprocess.run(
        ["bash", str(ROOT / "deploy/docker/add-worker.sh"), name],
        env=env, capture_output=True, text=True, timeout=20,
    )
    commands = (
        [json.loads(line)[1:] for line in calls.read_text().splitlines()] if calls.exists() else []
    )
    return result, [args for args in commands if args[0] == "run"]


def test_add_worker_reuses_image_and_token_without_volume(fake_commands):
    result, runs = add_worker(fake_commands, "tm-worker-3", TEST_TOKEN="b" * 32)
    assert result.returncode == 0, result.stderr
    (run,) = runs
    assert run[run.index("--name") + 1] == "tm-worker-3"
    assert run[-2:] == ["same-image", "ray-worker"]
    assert env_value(run, "TM_ARTIFACT_URL") == "http://tm-coordinator:8000"
    assert env_value(run, "TM_INTERNAL_TOKEN") == "b" * 32
    assert env_value(run, "TM_DATABASE_URL") is None
    assert not any("tm-artifacts" in arg for arg in run)
    # La imagen sale del head: la del coordinador lleva PostgreSQL.
    commands = [json.loads(line)[1:] for line in fake_commands[1].read_text().splitlines()]
    assert ["container", "inspect", "--format", "{{.Image}}", "tm-head"] in commands


@pytest.mark.parametrize(
    "name,extra",
    [
        ("tm-head", {"TEST_TOKEN": "b" * 32}),          # nombre no válido
        ("tm-worker-1", {"TEST_TOKEN": "b" * 32}),      # ya existe
        ("tm-worker-3", {"TEST_TOKEN": ""}),            # coordinador sin token
    ],
)
def test_add_worker_refuses_without_running(fake_commands, name, extra):
    result, runs = add_worker(fake_commands, name, **extra)
    assert result.returncode != 0
    assert runs == []


@pytest.mark.parametrize("mounted", ["", "yes"])
def test_artifact_probe_requires_download_without_shared_volume(fake_commands, mounted):
    env, calls = fake_commands
    env.update(TEST_OWNED="1", TEST_MOUNTED=mounted)
    result = subprocess.run(
        ["bash", str(ROOT / "deploy/docker/probe-artifact.sh"), "a" * 64],
        env=env,
        capture_output=True,
        text=True,
    )
    commands = [json.loads(line)[1:] for line in calls.read_text().splitlines()]
    checks = [args for args in commands if "verify-artifact" in args]
    if not mounted:
        assert result.returncode == 0, result.stderr
        assert [args[1] for args in checks] == [f"tm-worker-{i}" for i in range(1, 5)]
    else:
        assert result.returncode != 0
        assert not checks


def test_coordinator_entrypoint_honors_execution_backend(fake_commands):
    env, calls = fake_commands
    env["TM_BACKEND"] = "local"
    result = subprocess.run(
        ["bash", str(ROOT / "deploy/docker/entrypoint.sh"), "coordinator"],
        env=env,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    command = json.loads(calls.read_text().splitlines()[0])
    assert command[command.index("--backend") + 1] == "local"


@pytest.fixture
def local_database(tmp_path):
    # Dobles de PostgreSQL y tm en Python: atrapan SIGINT aunque bash lo ignore en
    # sus procesos en segundo plano, igual que el PostgreSQL real.
    program = (
        "#!" + str(Path(sys.executable).resolve()) + "\n"
        "import json, os, signal, sys, time\n"
        "name, args = os.path.basename(sys.argv[0]), sys.argv[1:]\n"
        "role = 'serve' if name == 'tm' and args[:1] == ['serve'] else name\n"
        "def log(text):\n"
        "    with open(os.environ['LOG'], 'a') as f:\n"
        "        f.write(text + '\\n')\n"
        "def stop(sig, _):\n"
        "    log(f'{role}-{signal.Signals(sig).name}')\n"
        "    sys.exit(0)\n"
        "signal.signal(signal.SIGINT, stop)\n"
        "signal.signal(signal.SIGTERM, stop)\n"
        "if name == 'setpriv':\n"
        "    rest = args[args.index('--') + 1:]\n"
        "    os.execvp(rest[0], rest)\n"
        "log(json.dumps([name, *args]))\n"
        "if name == 'initdb':\n"
        "    with open(os.path.join(args[args.index('-D') + 1], 'PG_VERSION'), 'w') as f:\n"
        "        f.write('17\\n')\n"
        "if role in ('postgres', 'serve'):\n"
        "    code = os.environ.get('FAKE_EXIT_' + role.upper())\n"
        "    if code:\n"
        "        time.sleep(3)\n"
        "        log(role + '-exit')\n"
        "        sys.exit(int(code))\n"
        "    while True:\n"
        "        time.sleep(0.1)\n"
    )
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    for name in ("setpriv", "chown", "initdb", "postgres", "pg_isready", "psql", "createdb", "tm"):
        path = bin_dir / name
        path.write_text(program)
        path.chmod(0o755)
    data = tmp_path / "data"
    data.mkdir()
    log = tmp_path / "log"
    env = {key: value for key, value in os.environ.items() if not key.startswith("TM_")}
    env.update(
        PATH=str(bin_dir) + ":" + env["PATH"], LOG=str(log), TM_PGDATA=str(data), PG_MAJOR="17",
        TM_DATABASE_URL="postgresql:///tournament_management?host=/run/postgresql&user=tm",
    )
    return env, data, log


def coordinator_with_local_database(env):
    return subprocess.Popen(
        ["bash", str(ROOT / "deploy/docker/entrypoint.sh"), "coordinator"],
        env=env, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True,
    )


def logged(log):
    return log.read_text().splitlines() if log.exists() else []


def test_local_database_starts_before_the_server_and_stops_after_it(local_database):
    env, _, log = local_database
    process = coordinator_with_local_database(env)
    deadline = time.monotonic() + 15
    while not any(line.startswith('["tm", "serve"') for line in logged(log)):
        assert process.poll() is None and time.monotonic() < deadline, process.stderr.read()
        time.sleep(0.1)
    process.send_signal(signal.SIGTERM)  # lo que hace docker stop
    assert process.wait(timeout=15) == 0, process.stderr.read()
    lines = logged(log)
    calls = [json.loads(line) for line in lines if line.startswith("[")]
    initdb = next(call for call in calls if call[0] == "initdb")
    assert initdb[initdb.index("-A") + 1] == "trust"
    postgres = next(call for call in calls if call[0] == "postgres")
    assert "listen_addresses=" in postgres  # sin puerto: solo el socket
    assert any(call[0] == "createdb" for call in calls)
    order = [call[:2] for call in calls if call[0] == "tm"]
    assert order == [["tm", "migrate"], ["tm", "serve"]]
    # Primero el servidor, después la base: nadie escribe con la base parada.
    assert lines[-2:] == ["serve-SIGTERM", "postgres-SIGINT"]


def test_local_database_of_another_version_is_never_used(local_database):
    env, data, log = local_database
    (data / "PG_VERSION").write_text("16\n")
    process = coordinator_with_local_database(env)
    assert process.wait(timeout=15) == 1
    assert "PostgreSQL 16" in process.stderr.read()
    assert not any(line.startswith(('["initdb"', '["postgres"', '["tm"')) for line in logged(log))
    assert (data / "PG_VERSION").read_text() == "16\n"


def test_server_stops_when_local_database_dies(local_database):
    env, _, log = local_database
    process = coordinator_with_local_database({**env, "FAKE_EXIT_POSTGRES": "7"})
    assert process.wait(timeout=20) == 1
    assert "PostgreSQL terminó" in process.stderr.read()
    assert logged(log)[-2:] == ["postgres-exit", "serve-SIGTERM"]


def test_local_database_stops_when_server_ends(local_database):
    env, _, log = local_database
    process = coordinator_with_local_database({**env, "FAKE_EXIT_SERVE": "3"})
    assert process.wait(timeout=20) == 3
    assert logged(log)[-2:] == ["serve-exit", "postgres-SIGINT"]
