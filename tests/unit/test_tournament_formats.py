from itertools import combinations

import pytest

from tournament_management.shared.messages import AttemptOutcome, OutcomeKind
from tournament_management.simulation.tournament_service import Competitor, run_tournament
from tournament_management.tournaments.formats.elimination import Elimination
from tournament_management.tournaments.formats.round_robin import RoundRobin


@pytest.mark.parametrize("size", [2, 3, 5, 16])
def test_elimination_with_byes_and_dependencies(size):
    tournament = Elimination(tuple(f"p{i}" for i in range(size)))
    played = 0
    while tournament.status == "running":
        pending = tournament.pending()
        assert pending
        for pairing in pending:
            tournament.record(pairing.match_id, pairing.competitors[0])
            played += 1
    assert played == size - 1
    assert tournament.champion == "p0"


@pytest.mark.parametrize("size", [2, 3, 5, 16])
def test_round_robin_unique_pairs_and_deterministic_ties(size):
    players = tuple(f"p{i}" for i in range(size))
    tournament = RoundRobin(players)
    pending = tournament.pending()
    assert len(pending) == size * (size - 1) // 2
    assert {pairing.competitors for pairing in pending} == set(combinations(players, 2))
    for pairing in pending:
        tournament.record(pairing.match_id, pairing.competitors[0])
    assert tournament.status == "completed"
    assert tournament.standings[0] == ("p0", size - 1)
    assert tournament.standings[-1] == (players[-1], 0)


@pytest.mark.parametrize("format_class", [Elimination, RoundRobin])
def test_no_winner_blocks_without_scheduling_more(format_class):
    tournament = format_class(("a", "b", "c"))
    first = tournament.pending()[0]
    tournament.record(first.match_id, None)
    assert tournament.status == "blocked"
    assert tournament.pending() == ()
    with pytest.raises(ValueError):
        tournament.record(first.match_id, "a")


def test_rejects_duplicate_and_unknown_winner():
    tournament = RoundRobin(("a", "b", "c"))
    first = tournament.pending()[0]
    with pytest.raises(ValueError):
        tournament.record(first.match_id, "outsider")
    tournament.record(first.match_id, "a")
    with pytest.raises(ValueError):
        tournament.record(first.match_id, "a")


def test_round_robin_cycle_tie_uses_registration_order():
    tournament = RoundRobin(("a", "b", "c"))
    tournament.record("m1", "a")  # a-b
    tournament.record("m2", "c")  # a-c
    tournament.record("m3", "b")  # b-c
    assert tournament.standings == (("a", 1), ("b", 1), ("c", 1))


def test_application_blocks_infrastructure_result():
    def execute(attempt, pairing):
        return AttemptOutcome(
            match_id=pairing.match_id, attempt_id=attempt.attempt_id,
            generation=1, worker_id="local", kind=OutcomeKind.INFRASTRUCTURE_FAILURE,
            winner_id="", reason="worker lost",
        )

    report = run_tournament(
        "t", "round_robin", tuple(Competitor(p, "a" * 64) for p in ("a", "b", "c")),
        (1, 2), 0, 42, (100, 1000, 10_000_000, 65536), execute,
    )
    assert report["status"] == "blocked"
    assert report["played"] == 1


@pytest.mark.parametrize("format_name,expected", [("elimination", 15), ("round_robin", 120)])
def test_application_simulates_sixteen_healthy_competitors(format_name, expected):
    seen = set()

    def execute(attempt, pairing):
        assert pairing.match_id not in seen
        seen.add(pairing.match_id)
        assert attempt.match.competitor_ids == pairing.competitors
        return AttemptOutcome(
            match_id=pairing.match_id, attempt_id=attempt.attempt_id,
            generation=attempt.generation, worker_id="local", kind=OutcomeKind.COMPLETED,
            winner_id=pairing.competitors[0], reason="last_move",
        )

    report = run_tournament(
        "t", format_name, tuple(Competitor(f"p{i}", "a" * 64) for i in range(16)),
        (1, 2), 0, 42, (100, 1000, 10_000_000, 65536), execute,
    )
    assert report["status"] == "completed"
    assert report["played"] == expected
    if format_name == "elimination":
        assert report["champion"] == "p0"
    else:
        assert len(report["standings"]) == 16
