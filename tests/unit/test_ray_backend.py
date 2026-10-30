"""Backend Ray con un Ray falso: casos raros y deterministas (D1.5).

El comportamiento con Ray real se prueba en tests/integration/test_ray_execution.py.
"""

import sys
import threading
from dataclasses import dataclass, replace
from types import ModuleType

import pytest

from tournament_management.execution.backend import ExecutionError
from tournament_management.shared.messages import (
    AttemptOutcome,
    AttemptSpec,
    MatchSpec,
    OutcomeKind,
    WorkerStatus,
)


class WorkerCrashedError(Exception):
    """Mismo nombre que la excepción de Ray: el adaptador solo usa el nombre."""


@dataclass(frozen=True)
class NodeAffinitySchedulingStrategy:
    node_id: str
    soft: bool


class FakeRef:
    def __init__(self, number):
        self.number = number

    def hex(self):
        return f"{self.number:08x}"


class FakeTask:
    def __init__(self, ray, decorator):
        self.ray, self.decorator, self.options_used = ray, decorator, {}

    def options(self, **options):
        task = FakeTask(self.ray, self.decorator)
        task.options_used = options
        return task

    def remote(self, *args):
        ref = FakeRef(len(self.ray.calls) + 1)
        self.ray.calls.append((self.decorator, self.options_used, args, ref.hex()))
        return ref


class FakeRay(ModuleType):
    def __init__(self):
        super().__init__("ray")
        self.calls, self.results, self.cancelled = [], {}, []

    def remote(self, **decorator):
        return lambda function: FakeTask(self, decorator)

    def wait(self, refs, num_returns, timeout):
        assert timeout == 0 and num_returns == len(refs)
        # Ray Client devuelve objetos nuevos: el adaptador debe emparejar por ID.
        return [FakeRef(r.number) for r in refs if r.hex() in self.results], []

    def get(self, ref, timeout):
        value = self.results[ref.hex()]
        if isinstance(value, Exception):
            raise value
        return value

    def cancel(self, ref, force):
        self.cancelled.append((ref.hex(), force))

    def finish(self, index, value):
        self.results[self.calls[index][3]] = value


NODES = [
    {"node_id": "h", "name": "tm-head", "worker": False, "slots": 0},
    {"node_id": "n2", "name": "tm-worker-2", "worker": True, "slots": 1},
    {"node_id": "n1", "name": "tm-worker-1", "worker": True, "slots": 1},
]


@pytest.fixture
def ray(monkeypatch):
    fake = FakeRay()
    strategies = ModuleType("ray.util.scheduling_strategies")
    strategies.NodeAffinitySchedulingStrategy = NodeAffinitySchedulingStrategy
    monkeypatch.setitem(sys.modules, "ray", fake)
    monkeypatch.setitem(sys.modules, "ray.util", ModuleType("ray.util"))
    monkeypatch.setitem(sys.modules, "ray.util.scheduling_strategies", strategies)
    return fake


class Clock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now


@pytest.fixture
def clock():
    return Clock()


@pytest.fixture
def backend(ray, clock):
    from tournament_management.execution.backends.ray import RayExecution

    nodes = list(NODES)
    instance = RayExecution(
        lambda: nodes, cancel_grace_ms=20, suspect_cooldown_ms=45_000, clock=clock
    )
    instance.nodes = nodes
    yield instance
    instance.close()


def attempt(number=1):
    return AttemptSpec(
        attempt_id=f"a{number}", generation=number,
        match=MatchSpec(
            tournament_id="t", match_id="m", competitor_ids=("x", "y"),
            artifact_hashes=("a" * 64, "b" * 64), initial_piles=(3,), first_player=0, seed=1,
        ),
        turn_timeout_ms=1000, match_timeout_ms=2000,
        memory_limit_bytes=1 << 28, output_limit_bytes=1 << 20,
    )


def outcome(spec, worker="tm-worker-1", winner="x"):
    return AttemptOutcome(
        match_id=spec.match.match_id, attempt_id=spec.attempt_id, generation=spec.generation,
        worker_id=worker, kind=OutcomeKind.COMPLETED, winner_id=winner, reason="last_move",
    )


def test_submit_pins_worker_without_ray_retries_and_sends_json(ray, backend):
    spec = attempt()
    backend.submit(spec)
    decorator, options, args, _ = ray.calls[0]
    assert decorator == {"max_retries": 0, "retry_exceptions": False}
    assert options["num_cpus"] == 1 and options["resources"] == {"tm_worker": 1}
    assert options["scheduling_strategy"] == NodeAffinitySchedulingStrategy("n1", soft=False)
    assert AttemptSpec.from_json(args[0]) == spec
    # Ni almacén ni token viajan en la tarea: el worker los toma de su entorno.
    assert args[1:] == ("tm-worker-1",)
    assert backend.worker_of(spec.attempt_id) == "tm-worker-1"


def test_submit_is_idempotent_and_rejects_reused_id(ray, backend):
    spec = attempt()
    backend.submit(spec)
    backend.submit(spec)
    assert len(ray.calls) == 1
    with pytest.raises(ExecutionError):
        backend.submit(replace(spec, generation=5))


def test_sequential_attempts_alternate_workers(ray, backend):
    first, second = attempt(1), attempt(2)
    backend.submit(first)
    ray.finish(0, outcome(first).to_json())
    assert backend.poll() == (outcome(first),)
    backend.acknowledge(first.attempt_id)
    backend.submit(second)
    assert ray.calls[1][2][1] == "tm-worker-2"


def test_no_capacity_until_a_worker_finishes(ray, backend):
    backend.submit(attempt(1))
    backend.submit(attempt(2))
    with pytest.raises(ExecutionError, match="capacidad"):
        backend.submit(attempt(3))
    ray.finish(0, outcome(attempt(1)).to_json())
    backend.submit(attempt(3))
    assert ray.calls[2][2][1] == "tm-worker-1"


def test_notification_repeats_until_acknowledged(ray, backend):
    spec = attempt()
    backend.submit(spec)
    assert backend.poll() == ()
    ray.finish(0, outcome(spec).to_json())
    assert backend.poll() == backend.poll() == (outcome(spec),)
    backend.acknowledge(spec.attempt_id)
    backend.acknowledge(spec.attempt_id)
    assert backend.poll() == ()
    assert backend.worker_of(spec.attempt_id) == ""


@pytest.mark.parametrize(
    "value,reason",
    [
        (WorkerCrashedError("worker died"), "ray:WorkerCrashedError"),
        ("not-json", "invalid_outcome:JSONDecodeError"),
        (outcome(attempt(2)).to_json(), "invalid_outcome:ValueError"),
    ],
)
def test_failures_become_infrastructure_on_the_assigned_worker(ray, backend, value, reason):
    spec = attempt()
    backend.submit(spec)
    ray.finish(0, value)
    (result,) = backend.poll()
    assert result.kind is OutcomeKind.INFRASTRUCTURE_FAILURE
    assert (result.worker_id, result.winner_id, result.reason) == ("tm-worker-1", "", reason)
    result.validate_for(spec)


def test_cancel_is_soft_first_and_forced_after_grace(ray, backend):
    spec = attempt()
    backend.submit(spec)
    backend.cancel(spec.attempt_id)
    backend.cancel(spec.attempt_id)
    ref = ray.calls[0][3]
    assert ray.cancelled == [(ref, False)]
    deadline = threading.Event()
    for _ in range(100):
        if (ref, True) in ray.cancelled:
            break
        deadline.wait(0.01)
    assert ray.cancelled == [(ref, False), (ref, True)]


def test_cancel_not_forced_if_attempt_ends_within_grace(ray, backend):
    spec = attempt()
    backend.submit(spec)
    backend.cancel(spec.attempt_id)
    ray.finish(0, WorkerCrashedError("cancelled"))
    threading.Event().wait(0.1)
    assert ray.cancelled == [(ray.calls[0][3], False)]


def test_workers_use_public_names_and_count_running(ray, backend):
    backend.submit(attempt())
    workers = backend.workers()
    assert [(w.worker_id, w.capacity, w.running_attempts) for w in workers] == [
        ("tm-worker-1", 1, 1), ("tm-worker-2", 1, 0),
    ]
    backend.nodes.pop()
    # El worker desaparecido sigue listado como perdido y sin capacidad.
    assert [(w.worker_id, w.status, w.capacity) for w in backend.workers()] == [
        ("tm-worker-1", WorkerStatus.LOST, 0), ("tm-worker-2", WorkerStatus.AVAILABLE, 1),
    ]


def status_of(backend, name):
    return {w.worker_id: w.status for w in backend.workers()}[name]


def test_worker_death_makes_worker_suspected_and_next_attempt_goes_elsewhere(ray, backend):
    backend.submit(attempt(1))
    ray.finish(0, WorkerCrashedError("node died"))
    backend.poll()
    backend.acknowledge("a1")
    # Ray aún lo lista vivo, pero no recibe trabajo mientras sea sospechoso.
    assert status_of(backend, "tm-worker-1") is WorkerStatus.SUSPECTED
    backend.submit(attempt(2))
    assert ray.calls[1][2][1] == "tm-worker-2"
    with pytest.raises(ExecutionError, match="capacidad"):
        backend.submit(attempt(3))


def test_suspicion_ends_after_cooldown(ray, backend, clock):
    backend.submit(attempt(1))
    ray.finish(0, WorkerCrashedError("node died"))
    backend.poll()
    backend.acknowledge("a1")
    clock.now += 45.1
    assert status_of(backend, "tm-worker-1") is WorkerStatus.AVAILABLE


def test_restarted_worker_is_available_again(ray, backend):
    backend.submit(attempt(1))
    ray.finish(0, WorkerCrashedError("node died"))
    backend.poll()
    backend.acknowledge("a1")
    backend.nodes[2] = {**backend.nodes[2], "node_id": "n1-restarted"}
    assert status_of(backend, "tm-worker-1") is WorkerStatus.AVAILABLE


def test_our_own_cancellation_does_not_make_worker_suspected(ray, backend):
    backend.submit(attempt(1))
    backend.cancel("a1")
    ray.finish(0, WorkerCrashedError("cancelled"))
    backend.poll()
    assert status_of(backend, "tm-worker-1") is WorkerStatus.AVAILABLE


def test_unreadable_result_does_not_make_worker_suspected(ray, backend):
    backend.submit(attempt(1))
    ray.finish(0, "not-json")
    backend.poll()
    assert status_of(backend, "tm-worker-1") is WorkerStatus.AVAILABLE


@pytest.mark.parametrize(
    "environment",
    [{}, {"TM_ARTIFACT_URL": "http://tm-coordinator:8000"}, {"TM_ARTIFACT_URL": "ftp://x"}],
)
def test_worker_without_download_settings_reports_infrastructure(monkeypatch, environment):
    from tournament_management.execution.backends.ray import play_attempt

    for name in ("TM_ARTIFACT_URL", "TM_INTERNAL_TOKEN"):
        monkeypatch.delenv(name, raising=False)
    for name, value in environment.items():
        monkeypatch.setenv(name, value)
    spec = attempt()
    result = AttemptOutcome.from_json(play_attempt(spec.to_json(), "tm-worker-1"))
    result.validate_for(spec)
    assert result.kind is OutcomeKind.INFRASTRUCTURE_FAILURE
    assert result.reason == "artifact_download_not_configured"


def test_membership_error_is_translated(ray):
    from tournament_management.execution.backends.ray import RayExecution

    def broken():
        raise RuntimeError("gcs unavailable")

    instance = RayExecution(broken, cancel_grace_ms=1)
    with pytest.raises(ExecutionError):
        instance.workers()
    with pytest.raises(ExecutionError):
        instance.submit(attempt())
