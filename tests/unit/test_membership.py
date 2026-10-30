"""Estados propios de los workers con un reloj falso (4.3, D3.1)."""

import pytest

from tournament_management.execution.membership import WorkerMembership
from tournament_management.shared.messages import WorkerStatus

AVAILABLE, SUSPECTED, LOST = WorkerStatus.AVAILABLE, WorkerStatus.SUSPECTED, WorkerStatus.LOST


class Clock:
    def __init__(self):
        self.now = 100.0

    def __call__(self):
        return self.now


@pytest.fixture
def clock():
    return Clock()


@pytest.fixture
def membership(clock):
    members = WorkerMembership(cooldown_ms=45_000, clock=clock)
    members.observe([("tm-worker-1", "n1", 1), ("tm-worker-2", "n2", 1)])
    return members


def test_new_workers_are_available_and_sorted(membership):
    assert membership.names() == ["tm-worker-1", "tm-worker-2"]
    assert {membership.status(n) for n in membership.names()} == {AVAILABLE}
    assert (membership.node_id("tm-worker-2"), membership.slots("tm-worker-2")) == ("n2", 1)


def test_infrastructure_failure_makes_worker_suspected_until_cooldown(membership, clock):
    membership.suspect("tm-worker-1", "n1")
    assert membership.status("tm-worker-1") is SUSPECTED
    clock.now += 44.9
    membership.observe([("tm-worker-1", "n1", 1), ("tm-worker-2", "n2", 1)])
    assert membership.status("tm-worker-1") is SUSPECTED
    clock.now += 0.2
    assert membership.status("tm-worker-1") is AVAILABLE
    assert membership.status("tm-worker-2") is AVAILABLE


def test_missing_worker_is_lost_even_if_suspected(membership):
    membership.suspect("tm-worker-1", "n1")
    membership.observe([("tm-worker-2", "n2", 1)])
    assert membership.status("tm-worker-1") is LOST
    assert "tm-worker-1" in membership.names()


def test_restart_with_new_identity_clears_suspicion(membership):
    membership.suspect("tm-worker-1", "n1")
    membership.observe([("tm-worker-1", "n1-restarted", 1), ("tm-worker-2", "n2", 1)])
    assert membership.status("tm-worker-1") is AVAILABLE
    assert membership.node_id("tm-worker-1") == "n1-restarted"


def test_failure_of_old_identity_does_not_suspect_restarted_worker(membership):
    # Docker 5.1 (E1.14): el intento fue al proceso viejo y falló tras el reinicio.
    membership.observe([("tm-worker-1", "n1-restarted", 1), ("tm-worker-2", "n2", 1)])
    membership.suspect("tm-worker-1", "n1")
    assert membership.status("tm-worker-1") is AVAILABLE


def test_lost_worker_that_returns_is_available(membership):
    membership.observe([("tm-worker-2", "n2", 1)])
    membership.observe([("tm-worker-1", "n1b", 1), ("tm-worker-2", "n2", 1)])
    assert membership.status("tm-worker-1") is AVAILABLE


def test_lost_or_unknown_worker_cannot_be_suspected(membership):
    membership.observe([("tm-worker-2", "n2", 1)])
    membership.suspect("tm-worker-1", "n1")
    membership.suspect("tm-worker-9", "n9")
    membership.observe([("tm-worker-1", "n1", 1), ("tm-worker-2", "n2", 1)])
    assert membership.status("tm-worker-1") is AVAILABLE
    assert "tm-worker-9" not in membership.names()


def test_new_worker_joins(membership):
    membership.observe([("tm-worker-1", "n1", 1), ("tm-worker-2", "n2", 1),
                        ("tm-worker-3", "n3", 2)])
    assert membership.names()[-1] == "tm-worker-3"
    assert (membership.status("tm-worker-3"), membership.slots("tm-worker-3")) == (AVAILABLE, 2)


@pytest.mark.parametrize("order", ["old-first", "new-first"])
def test_restart_while_old_identity_is_still_listed_picks_the_new_one(membership, order):
    # Docker 4.4: tras docker kill + docker start, Ray listó ~30 s el nodo viejo y el nuevo.
    nodes = [("tm-worker-1", "n1", 1), ("tm-worker-1", "n1-restarted", 1), ("tm-worker-2", "n2", 1)]
    if order == "new-first":
        nodes[0], nodes[1] = nodes[1], nodes[0]
    membership.observe(nodes)
    assert membership.node_id("tm-worker-1") == "n1-restarted"
    membership.observe(nodes)  # sigue eligiendo la nueva aunque Ray repita las dos
    assert membership.node_id("tm-worker-1") == "n1-restarted"
