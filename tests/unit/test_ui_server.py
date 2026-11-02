"""Panel de ui/ (bloque G): lista cerrada de órdenes y controles de acceso."""

import importlib.util
import json
import sys
import threading
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

import pytest

PATH = Path(__file__).resolve().parents[2] / "ui/server.py"
spec = importlib.util.spec_from_file_location("ui_server", PATH)
ui = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ui)


def test_scenarios_come_from_the_folder_and_partition_is_docker_only():
    docker = ui.scenarios("docker")
    assert "worker-loss" in docker and "worker-partition" in docker
    assert "load" not in docker
    assert "worker-partition" not in ui.scenarios("local")


WORKERS = [{"name": "tm-worker-1", "up": True}, {"name": "tm-worker-2", "up": False}]
TOURNAMENT = {"format": "elimination", "size": 5, "bots": ["optimal", "faulty/timeout"],
              "piles": [3, 4, 5]}


def test_every_catalog_entry_builds_a_command():
    args = {"worker-next": "tm-worker-3", "worker-up": "tm-worker-1",
            "worker-down": "tm-worker-2", "tournament": TOURNAMENT}
    for mode in ("docker", "local"):
        for item in ui.catalog(mode):
            if item["arg"] == "results":
                continue  # depende de las carpetas que haya
            argv, env = ui.build_command(mode, item["id"], args.get(item["arg"]), WORKERS)
            assert env["TM_SCENARIO_MODE"] == mode
            assert all(isinstance(part, str) for part in argv)


def test_worker_commands_follow_the_cluster_state():
    assert ui.next_worker(WORKERS) == "tm-worker-3"
    argv, _ = ui.build_command("docker", "worker-fail", "tm-worker-1", WORKERS)
    assert argv[1:] == ["deploy/docker/fail-worker.sh", "tm-worker-1"]
    argv, _ = ui.build_command("docker", "worker-recover", "tm-worker-2", WORKERS)
    assert argv == ["docker", "start", "tm-worker-2"]
    argv, _ = ui.build_command("local", "worker-recover", "tm-worker-2", WORKERS)
    assert argv[1:] == ["deploy/local/local-cluster.sh", "restore", "tm-worker-2"]
    argv, _ = ui.build_command("local", "worker-add", "tm-worker-3", WORKERS)
    assert argv[1:] == ["deploy/local/local-cluster.sh", "add-worker", "3"]
    bad = [("worker-add", "tm-worker-1"), ("worker-add", "tm-worker-4"),
           ("worker-fail", "tm-worker-2"), ("worker-recover", "tm-worker-1"),
           ("worker-fail", "tm-worker-1; rm -rf /"), ("worker-fail", None),
           ("worker-recover", "coordinator")]
    for command, arg in bad:
        with pytest.raises(ValueError):
            ui.build_command("docker", command, arg, WORKERS)


def test_custom_tournament_is_validated():
    argv, _ = ui.build_command("docker", "tournament", TOURNAMENT)
    assert argv[1:] == ["experiments/scenarios/load.sh", "torneo", "--mix", "elimination:5x1",
                        "--bot", "optimal,faulty/timeout", "--piles", "[3,4,5]"]
    for change in ({"format": "swiss"}, {"size": 1}, {"size": 17}, {"size": "4"},
                   {"bots": []}, {"bots": ["../../etc/passwd"]}, {"bots": ["optimal"] * 5},
                   {"piles": []}, {"piles": [0]}, {"piles": [51]}, {"piles": [1] * 6}):
        with pytest.raises(ValueError):
            ui.build_command("docker", "tournament", {**TOURNAMENT, **change})
    with pytest.raises(ValueError):
        ui.build_command("docker", "tournament", "optimal")


def test_result_labels():
    assert ui.result_label("worker-loss-20261101-011502") == "worker-loss · 01-11 01:15"
    assert ui.result_label("raro") == "raro"


def test_unknown_commands_and_paths_are_rejected():
    for command in ("", "rm", "scenario:../../evil", "scenario:load", "cluster-start;ls"):
        with pytest.raises(ValueError):
            ui.build_command("docker", command, None)
    with pytest.raises(ValueError):
        ui.build_command("local", "scenario:worker-partition", None)
    with pytest.raises(ValueError):
        ui.build_command("docker", "verify", "../../etc")


def test_pause_40_and_docker_paths_without_conversion():
    _, env = ui.build_command("docker", "scenario:worker-pause-40", None)
    assert env["TM_PAUSE_SECONDS"] == "40"
    _, env = ui.build_command("docker", "cluster-start", None)
    assert env["TM_BACKEND"] == "ray" and env["MSYS_NO_PATHCONV"] == "1"


def test_runner_allows_only_one_execution_at_a_time():
    runner = ui.Runner()
    runner.start("lenta", [sys.executable, "-c", "import time; print('hola'); time.sleep(2)"], {})
    with pytest.raises(RuntimeError):
        runner.start("otra", [sys.executable, "-c", "pass"], {})
    with runner.cond:
        runner.cond.wait_for(lambda: runner.run["exit_code"] is not None, timeout=10)
    assert runner.run["lines"] == ["hola"] and runner.run["exit_code"] == 0


@pytest.fixture
def server():
    ui.Handler.token = "secreto"
    ui.Handler.mode = "docker"
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), ui.Handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    yield httpd.server_address[1]
    httpd.shutdown()
    httpd.server_close()


def call(port, path, method="GET", headers=None, body=None):
    request = urllib.request.Request(f"http://127.0.0.1:{port}{path}", method=method,
                                     headers=headers or {}, data=body)
    try:
        with urllib.request.urlopen(request, timeout=5) as response:
            return response.status
    except urllib.error.HTTPError as error:
        return error.code


def test_access_controls(server):
    port = server
    assert call(port, "/") == 403                                    # sin token
    assert call(port, "/?token=otro") == 403
    assert call(port, "/?token=secreto") == 200
    assert call(port, "/?token=secreto", headers={"Host": f"evil.test:{port}"}) == 403
    run = json.dumps({"command": "cluster-status"}).encode()
    json_header = {"Content-Type": "application/json", "X-TM-UI-Token": "secreto"}
    assert call(port, "/api/run", "POST", json_header, run) == 403   # sin Origin
    evil = {**json_header, "Origin": "http://evil.test"}
    assert call(port, "/api/run", "POST", evil, run) == 403
    own = {**json_header, "Origin": f"http://127.0.0.1:{port}"}
    bad = json.dumps({"command": "bash -c id"}).encode()
    assert call(port, "/api/run", "POST", own, bad) == 400
    assert call(port, "/api/logs?token=secreto&source=../../x") == 400
