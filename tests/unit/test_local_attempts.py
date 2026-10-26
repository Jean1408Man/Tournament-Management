import threading
import time
from dataclasses import replace

import pytest

from tournament_management.execution.backend import ExecutionError
from tournament_management.execution.backends.local import LocalExecution
from tournament_management.shared.messages import (
    AttemptOutcome,
    AttemptSpec,
    MatchSpec,
    OutcomeKind,
)
from tournament_management.simulation.result_service import ResultService
from tournament_management.simulation.scheduler import Scheduler
from tournament_management.simulation.tournament_service import Competitor, run_tournament


def attempt():
    return AttemptSpec(
        attempt_id="a1", generation=1,
        match=MatchSpec(
            tournament_id="t1", match_id="m1", competitor_ids=("a", "b"),
            artifact_hashes=("a" * 64, "b" * 64), initial_piles=(1, 2),
            first_player=0, seed=42,
        ),
        turn_timeout_ms=100, match_timeout_ms=1000,
        memory_limit_bytes=1_000_000, output_limit_bytes=65536,
    )


def outcome(spec, kind=OutcomeKind.COMPLETED, winner="a"):
    return AttemptOutcome(
        match_id=spec.match.match_id, attempt_id=spec.attempt_id,
        generation=spec.generation, worker_id="local", kind=kind,
        winner_id=winner, reason="test",
    )


def test_old_result_discarded_even_if_it_arrives_before_new_result():
    results = ResultService()
    old = attempt()
    newer = replace(old, attempt_id="a2", generation=2)
    results.issue(old)
    results.invalidate("m1", "a1")
    results.issue(newer)
    assert not results.accept(outcome(old))
    assert results.accept(outcome(newer, winner="b"))
    assert not results.accept(outcome(old))
    assert not results.accept(outcome(newer, winner="b"))
    assert results.accepted("m1") == outcome(newer, winner="b")


def test_cancellation_serializes_with_acceptance():
    results = ResultService()
    spec = attempt()
    results.issue(spec)
    results.cancel("m1")
    assert not results.accept(outcome(spec))
    with pytest.raises(ValueError):
        results.issue(replace(spec, attempt_id="a2", generation=2))


def test_backend_retains_notification_and_rejects_conflicting_submission():
    spec = attempt()
    with LocalExecution(lambda current, cancel: outcome(current)) as backend:
        backend.submit(spec)
        backend.submit(spec)
        with pytest.raises(ExecutionError):
            backend.submit(replace(spec, generation=2))
        deadline = time.monotonic() + 2
        while not backend.poll() and time.monotonic() < deadline:
            time.sleep(0.01)
        assert backend.poll() == (outcome(spec),)
        assert backend.poll() == (outcome(spec),)
        backend.acknowledge(spec.attempt_id)
        backend.acknowledge(spec.attempt_id)
        assert backend.poll() == ()


def test_scheduler_retries_infrastructure_with_new_identity():
    submitted = []

    def execute(current, cancel):
        submitted.append(current)
        if current.generation == 1:
            return outcome(current, OutcomeKind.INFRASTRUCTURE_FAILURE, "")
        return outcome(current, winner="b")

    with LocalExecution(execute) as backend:
        results = ResultService()
        accepted = Scheduler(backend, results, max_retries=1, attempt_timeout_ms=1000).run_match(
            attempt()
        )
        assert accepted.winner_id == "b"
        assert accepted.generation == 2
        assert submitted[0].attempt_id != submitted[1].attempt_id
        assert results.accepted("m1") == accepted


def test_scheduler_cancels_running_attempt():
    started = threading.Event()

    def execute(current, cancel):
        started.set()
        cancel.wait(2)
        return outcome(current)

    with LocalExecution(execute) as backend:
        results = ResultService()
        scheduler = Scheduler(backend, results, attempt_timeout_ms=1000)
        holder = []
        thread = threading.Thread(target=lambda: holder.append(scheduler.run_match(attempt())))
        thread.start()
        assert started.wait(1)
        scheduler.cancel("m1")
        thread.join(timeout=2)
        assert not thread.is_alive()
        assert holder[0].kind == OutcomeKind.INFRASTRUCTURE_FAILURE
        assert results.accepted("m1") is None


def test_scheduler_discards_old_notification_arriving_before_new_result():
    class OutOfOrderBackend:
        def __init__(self):
            self.submitted = []
            self.acknowledged = []

        def submit(self, spec):
            self.submitted.append(spec)

        def poll(self):
            if len(self.submitted) == 1:
                return (outcome(self.submitted[0], OutcomeKind.INFRASTRUCTURE_FAILURE, ""),)
            if len(self.submitted) == 2:
                old, new = self.submitted
                self.submitted.append(new)  # Only deliver this batch once.
                return (outcome(old), outcome(new, winner="b"))
            return ()

        def acknowledge(self, attempt_id):
            self.acknowledged.append(attempt_id)

        def cancel(self, attempt_id):
            pass

    backend = OutOfOrderBackend()
    results = ResultService()
    accepted = Scheduler(backend, results, max_retries=1, attempt_timeout_ms=100).run_match(
        attempt()
    )
    assert accepted.generation == 2
    assert accepted.winner_id == "b"
    assert results.accepted("m1") == accepted
    assert backend.acknowledged == ["a1", "a1", accepted.attempt_id]


def test_scheduler_exhausts_retry_budget_without_sporting_winner():
    class SilentBackend:
        def __init__(self):
            self.submitted = []
            self.cancelled = []

        def submit(self, spec):
            self.submitted.append(spec)

        def poll(self):
            return ()

        def cancel(self, attempt_id):
            self.cancelled.append(attempt_id)

    backend = SilentBackend()
    results = ResultService()
    final = Scheduler(backend, results, max_retries=1, attempt_timeout_ms=30).run_match(
        attempt()
    )
    assert final.kind is OutcomeKind.INFRASTRUCTURE_FAILURE
    assert final.winner_id == ""
    assert [spec.generation for spec in backend.submitted] == [1, 2]
    assert backend.cancelled == [spec.attempt_id for spec in backend.submitted]
    assert not results.accept(outcome(backend.submitted[0]))


def test_tournament_accepts_result_from_retry_through_local_backend():
    def execute(current, cancel):
        if current.generation == 1:
            return outcome(current, OutcomeKind.INFRASTRUCTURE_FAILURE, "")
        return outcome(current, winner=current.match.competitor_ids[0])

    with LocalExecution(execute) as backend:
        scheduler = Scheduler(backend, ResultService(), max_retries=1, attempt_timeout_ms=1000)
        report = run_tournament(
            "t", "elimination", (Competitor("a", "a" * 64), Competitor("b", "b" * 64)),
            (1, 2), 0, 42, (100, 1000, 10_000_000, 65536),
            lambda current, pairing: scheduler.run_match(current),
        )
    assert report["status"] == "completed"
    assert report["champion"] == "a"
    assert report["played"] == 1


def test_scheduler_keeps_accepted_result_when_batch_contains_duplicate():
    class DuplicateBackend:
        def submit(self, spec):
            self.spec = spec

        def poll(self):
            return (outcome(self.spec), outcome(self.spec))

        def acknowledge(self, attempt_id):
            pass

        def cancel(self, attempt_id):
            pass

    results = ResultService()
    accepted = Scheduler(
        DuplicateBackend(), results, max_retries=0, attempt_timeout_ms=100,
    ).run_match(attempt())
    assert accepted.kind is OutcomeKind.COMPLETED
    assert results.accepted("m1") == accepted
