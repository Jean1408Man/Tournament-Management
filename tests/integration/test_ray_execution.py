"""Backend Ray contra un Ray real local: un head sin CPU y dos workers (D1.5).

Usa Ray Client como el coordinador desplegado. Los nodos son procesos locales:
quitar uno simula que un worker muere. Los casos raros viven en
tests/unit/test_ray_backend.py con un Ray falso.
"""

import os
import socket
import time
from pathlib import Path

import pytest

from tournament_management.cluster.ray_connection import RayConnection
from tournament_management.competitors.artifact_store import FileArtifactStore
from tournament_management.shared.messages import AttemptSpec, MatchSpec, OutcomeKind

ROOT = Path(__file__).resolve().parents[2]
NAMES = ("tm-worker-1", "tm-worker-2")
STORE_BYTES = 80 * 1024 * 1024


@pytest.fixture(scope="module")
def cluster():
    with pytest.MonkeyPatch.context() as patch:
        # Misma mitigación que el despliegue (decisión 1.15): sin ella el servidor
        # Ray Client por conexión puede abortar al arrancar dentro de pytest.
        patch.setenv("GRPC_POLL_STRATEGY", "poll")
        patch.setenv("GRPC_ENABLE_FORK_SUPPORT", "true")
        yield from _cluster()


def _cluster():
    from ray.cluster_utils import Cluster

    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    cluster = Cluster(
        initialize_head=True,
        head_node_args={
            "num_cpus": 0, "ray_client_server_port": port,
            "object_store_memory": STORE_BYTES, "include_dashboard": False,
        },
    )
    nodes = {
        name: cluster.add_node(
            num_cpus=1, resources={"tm_worker": 1}, node_name=name,
            object_store_memory=STORE_BYTES,
        )
        for name in NAMES
    }
    cluster.wait_for_nodes()
    connection = RayConnection(f"ray://127.0.0.1:{port}")
    connection.connect()
    yield cluster, nodes, connection
    connection.close()
    cluster.shutdown()


@pytest.fixture(scope="module")
def store(tmp_path_factory):
    return FileArtifactStore(tmp_path_factory.mktemp("artifacts"))


@pytest.fixture
def backend(cluster, store):
    from tournament_management.execution.backends.ray import RayExecution

    instance = RayExecution(
        cluster[2].snapshot, artifact_dir=str(store.directory),
        artifact_max_bytes=store.max_bytes, cancel_grace_ms=2000,
    )
    yield instance
    instance.close()


def attempt(store, number, bots, *, turn_timeout_ms=2000):
    hashes = tuple(store.put((ROOT / bot).read_bytes()) for bot in bots)
    return AttemptSpec(
        attempt_id=f"ray-{number}-{time.monotonic_ns()}", generation=1,
        match=MatchSpec(
            tournament_id="t", match_id=f"m{number}", competitor_ids=("x", "y"),
            artifact_hashes=hashes, initial_piles=(3, 5, 7), first_player=0, seed=1,
        ),
        turn_timeout_ms=turn_timeout_ms, match_timeout_ms=2 * turn_timeout_ms,
        # El intérprete necesita más espacio virtual que el presupuesto de producción.
        memory_limit_bytes=1 << 30, output_limit_bytes=1 << 20,
    )


def wait_outcome(backend, spec, seconds):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        for outcome in backend.poll():
            if outcome.attempt_id == spec.attempt_id:
                outcome.validate_for(spec)
                return outcome
        time.sleep(0.05)
    pytest.fail(f"Sin resultado de {spec.attempt_id} en {seconds} s")


def bot_processes(spec):
    """Procesos de bots vivos de este intento (sus copias viven en tm-attempt-*)."""
    found = []
    for pid in filter(str.isdigit, os.listdir("/proc")):
        try:
            command = Path(f"/proc/{pid}/cmdline").read_bytes()
        except OSError:
            continue
        if b"tm-attempt-" in command and b"player-" in command:
            found.append(pid)
    return found


def test_matches_run_on_both_workers_with_public_names(backend, store):
    seen = set()
    for number in (1, 2):
        spec = attempt(store, number, ("bots/optimal.py", "bots/random_bot.py"))
        backend.submit(spec)
        outcome = wait_outcome(backend, spec, 60)
        assert outcome.kind is OutcomeKind.COMPLETED
        assert outcome.worker_id == backend.worker_of(spec.attempt_id)
        backend.acknowledge(spec.attempt_id)
        seen.add(outcome.worker_id)
    assert seen == set(NAMES)
    assert sorted(w.worker_id for w in backend.workers()) == list(NAMES)


def test_soft_cancel_stops_attempt_and_reaps_bots(backend, store):
    spec = attempt(
        store, 3, ("bots/faulty/timeout.py", "bots/optimal.py"), turn_timeout_ms=60_000
    )
    backend.submit(spec)
    time.sleep(3)
    assert backend.poll() == ()
    backend.cancel(spec.attempt_id)
    outcome = wait_outcome(backend, spec, 30)
    assert outcome.kind is OutcomeKind.INFRASTRUCTURE_FAILURE
    assert outcome.reason.startswith("ray:")
    print(f"\ncancelación observada: {outcome.reason}")
    time.sleep(1)
    assert bot_processes(spec) == []


def test_worker_death_is_infrastructure_failure_on_that_worker(cluster, backend, store):
    spec = attempt(
        store, 4, ("bots/faulty/timeout.py", "bots/optimal.py"), turn_timeout_ms=60_000
    )
    backend.submit(spec)
    time.sleep(3)
    worker = backend.worker_of(spec.attempt_id)
    started = time.monotonic()
    cluster[0].remove_node(cluster[1][worker], allow_graceful=False)
    outcome = wait_outcome(backend, spec, 120)
    print(f"\nmuerte de worker observada: {outcome.reason} "
          f"tras {time.monotonic() - started:.1f} s")
    assert outcome.kind is OutcomeKind.INFRASTRUCTURE_FAILURE
    assert outcome.worker_id == worker
    assert outcome.reason.startswith("ray:")
    deadline = time.monotonic() + 60
    while worker in {w.worker_id for w in backend.workers()}:
        assert time.monotonic() < deadline, "Ray sigue anunciando el worker muerto"
        time.sleep(0.5)
