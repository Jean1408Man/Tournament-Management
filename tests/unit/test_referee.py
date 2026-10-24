import sys
from pathlib import Path

from tournament_management.domain.messages import AttemptSpec, MatchSpec, OutcomeKind
from tournament_management.referee.runner import run_match

ROOT = Path(__file__).resolve().parents[2]


def attempt():
    return AttemptSpec(
        attempt_id="a1", generation=1,
        match=MatchSpec(
            tournament_id="t1", match_id="m1", competitor_ids=("optimal", "random"),
            artifact_hashes=("a" * 64, "b" * 64), initial_piles=(1, 2),
            first_player=0, seed=42,
        ),
        turn_timeout_ms=2_000, match_timeout_ms=10_000,
        # Python's interpreter and shared libraries need more virtual address
        # space than the small production budget; the referee still enforces
        # the configured value when launching real registered bots.
        memory_limit_bytes=1024 * 1024 * 1024, output_limit_bytes=64 * 1024,
    )


def command(path, *args):
    return (sys.executable, str(ROOT / path), *args)


def test_optimal_bot_wins_a_winning_position():
    result = run_match(
        attempt(),
        (command("bots/optimal.py"), command("bots/random_bot.py", "--seed", "7")),
    )
    assert result.kind is OutcomeKind.COMPLETED
    assert result.winner_id == "optimal"


def test_illegal_bot_loses_without_leaking_processes():
    result = run_match(
        attempt(),
        (command("bots/faulty/illegal_move.py"), command("bots/optimal.py")),
    )
    assert result.kind is OutcomeKind.BOT_FAILURE
    assert result.winner_id == "random"

