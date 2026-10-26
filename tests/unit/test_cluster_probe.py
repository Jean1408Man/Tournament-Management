"""Pruebas del diagnóstico con dobles; la ejecución real se valida en Docker."""

import platform
import sys
from types import ModuleType, SimpleNamespace
from unittest.mock import Mock

import pytest

from tournament_management import __version__
from tournament_management.cluster import ray_probe
from tournament_management.execution.backend import ExecutionError

NODES = [
    {"node_id": "head", "address": "h", "cpus": 0, "worker": False},
    {"node_id": "worker-1", "address": "w1", "cpus": 1, "worker": True},
    {"node_id": "worker-2", "address": "w2", "cpus": 1, "worker": True},
]
VERSIONS = {
    "application_version": __version__,
    "python_version": platform.python_version(),
    "ray_version": "2.40.0",
}


def result(node_id, nonce="test"):
    return {
        "node_id": node_id,
        "hostname": node_id,
        "pid": 123,
        "nonce": nonce,
        **VERSIONS,
    }


@pytest.fixture
def probe_backend(monkeypatch):
    connection = Mock()
    connection.snapshot.return_value = NODES
    monkeypatch.setattr(ray_probe, "RayConnection", Mock(return_value=connection))
    ray = ModuleType("ray")
    ray.__version__ = "2.40.0"
    ray.cancel = Mock()
    ray.get = Mock(side_effect=lambda refs, timeout: [result(node, nonce) for node, nonce in refs])
    calls = []

    def options(**kwargs):
        strategy = kwargs["scheduling_strategy"]

        def submit(nonce):
            calls.append((strategy.node_id, strategy.soft, kwargs["resources"]))
            return (strategy.node_id, nonce)

        return SimpleNamespace(remote=submit)

    task = SimpleNamespace(options=Mock(side_effect=options))
    decorator = Mock(return_value=task)
    ray.remote = Mock(return_value=decorator)
    strategy_module = ModuleType("ray.util.scheduling_strategies")

    class Strategy:
        def __init__(self, node_id, soft):
            self.node_id = node_id
            self.soft = soft

    strategy_module.NodeAffinitySchedulingStrategy = Strategy
    monkeypatch.setitem(sys.modules, "ray", ray)
    monkeypatch.setitem(sys.modules, "ray.util", ModuleType("ray.util"))
    monkeypatch.setitem(sys.modules, "ray.util.scheduling_strategies", strategy_module)
    return connection, ray, calls


def test_probe_targets_each_worker_with_hard_affinity(probe_backend):
    connection, ray, calls = probe_backend
    report = ray_probe.probe_cluster("ray://head:10001")
    assert report["status"] == "ok"
    assert [row["node_id"] for row in report["workers"]] == ["worker-1", "worker-2"]
    assert calls == [
        ("worker-1", False, {"tm_worker": 0.001}),
        ("worker-2", False, {"tm_worker": 0.001}),
    ]
    ray.remote.assert_called_once_with(num_cpus=1, max_retries=0)
    assert ray.get.call_args.kwargs["timeout"] == 30
    connection.close.assert_called_once()


def test_missing_worker_fails_before_sending_tasks(probe_backend):
    connection, ray, _ = probe_backend
    connection.snapshot.return_value = NODES[:2]
    with pytest.raises(ExecutionError, match="workers"):
        ray_probe.probe_cluster("ray://head:10001")
    ray.remote.assert_not_called()
    connection.close.assert_called_once()


def test_timeout_cancels_pending_tasks_and_disconnects(probe_backend):
    connection, ray, _ = probe_backend
    ray.get.side_effect = TimeoutError("worker perdido")
    with pytest.raises(ExecutionError, match="TimeoutError"):
        ray_probe.probe_cluster("ray://head:10001", timeout_seconds=1)
    assert ray.cancel.call_count == 2
    connection.close.assert_called_once()


def test_partial_submission_is_cleaned_up(probe_backend):
    connection, ray, _ = probe_backend
    task = ray.remote.return_value.return_value
    original = task.options.side_effect
    count = 0

    def options(**kwargs):
        nonlocal count
        count += 1
        if count == 2:
            raise RuntimeError("falló el segundo envío")
        return original(**kwargs)

    task.options.side_effect = options
    with pytest.raises(ExecutionError):
        ray_probe.probe_cluster("ray://head:10001")
    ray.cancel.assert_called_once()
    connection.close.assert_called_once()


@pytest.mark.parametrize("rows", [
    [result("worker-1"), result("worker-1")],
    [result("worker-1"), result("head")],
    [result("worker-1")],
    [result("worker-1"), result("worker-2", "otra-prueba")],
    [result("worker-1"), {**result("worker-2"), "application_version": "wrong"}],
    [result("worker-1"), {**result("worker-2"), "python_version": "wrong"}],
    [result("worker-1"), {**result("worker-2"), "ray_version": "wrong"}],
    [result("worker-1"), {**result("worker-2"), "pid": 0}],
])
def test_invalid_evidence_is_rejected(rows):
    with pytest.raises(ExecutionError):
        ray_probe.validate_probe_results(rows, {"worker-1", "worker-2"}, "test", VERSIONS)


@pytest.mark.parametrize("workers,timeout", [(0, 30), (2, 0), (-1, 30)])
def test_invalid_limits(probe_backend, workers, timeout):
    connection, _, _ = probe_backend
    with pytest.raises(ValueError):
        ray_probe.probe_cluster("ray://head:10001", workers, timeout)
    connection.connect.assert_not_called()
