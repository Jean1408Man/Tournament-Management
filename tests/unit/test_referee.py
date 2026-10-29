import sys
import threading
import time
from dataclasses import replace
from pathlib import Path

import pytest

from tournament_management.game.process_io import BotFault, BotPipes
from tournament_management.game.referee import run_match
from tournament_management.shared.messages import AttemptSpec, MatchSpec, OutcomeKind

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


@pytest.mark.parametrize(
    "faulty",
    [
        "invalid_output.py", "premature_exit.py", "too_much_output.py",
        "stderr_flood.py", "timeout.py",
    ],
)
def test_faulty_bot_loses_and_returns_within_deadline(faulty):
    started = time.monotonic()
    result = run_match(
        attempt(),
        (command(f"bots/faulty/{faulty}"), command("bots/optimal.py")),
    )
    assert result.kind is OutcomeKind.BOT_FAILURE
    assert result.winner_id == "random"
    assert time.monotonic() - started < 3


def test_both_bots_fail_without_inventing_winner():
    result = run_match(
        attempt(),
        (command("bots/faulty/premature_exit.py"), command("bots/faulty/invalid_output.py")),
    )
    assert result.kind is OutcomeKind.NO_WINNER
    assert result.winner_id == ""


class _HelloFails(BotPipes):
    """El jugador 0 ya cerró stdin al enviar el saludo: fija el orden de la carrera."""

    def send(self, player, message, deadline, cancel):
        if player == 0 and message["type"] == "hello":
            raise BotFault(0, "El bot cerró stdin")
        super().send(player, message, deadline, cancel)


@pytest.mark.parametrize(
    "rival,kind,winner",
    [
        ("bots/faulty/invalid_output.py", OutcomeKind.NO_WINNER, ""),
        ("bots/optimal.py", OutcomeKind.BOT_FAILURE, "random"),
    ],
)
def test_bot_dead_before_hello_is_a_handshake_fault(monkeypatch, rival, kind, winner):
    monkeypatch.setattr("tournament_management.game.referee.BotPipes", _HelloFails)
    result = run_match(attempt(), (command("bots/faulty/premature_exit.py"), command(rival)))
    assert result.kind is kind
    assert result.winner_id == winner


def test_cancel_interrupts_silent_bot_and_reaps_processes():
    cancel = threading.Event()
    holder = []
    thread = threading.Thread(target=lambda: holder.append(run_match(
        attempt(), (command("bots/faulty/timeout.py"), command("bots/optimal.py")),
        cancel=cancel,
    )))
    thread.start()
    time.sleep(0.1)
    cancel.set()
    thread.join(timeout=2)
    assert not thread.is_alive()
    assert holder[0].kind is OutcomeKind.INFRASTRUCTURE_FAILURE


def test_memory_limit_rejects_bot_allocation():
    spec = replace(attempt(), memory_limit_bytes=64 * 1024 * 1024)
    result = run_match(
        spec, (command("bots/faulty/memory_hog.py"), command("bots/optimal.py")),
    )
    assert result.kind is OutcomeKind.BOT_FAILURE
    assert result.winner_id == "random"



def test_slow_bot_plays_legally_and_takes_half_a_second_per_move():
    spec = replace(attempt().match, competitor_ids=("slow-1", "slow-2"), initial_piles=(2, 1))
    started = time.monotonic()
    result = run_match(
        replace(attempt(), match=spec), (command("bots/slow.py"), command("bots/slow.py"))
    )
    elapsed = time.monotonic() - started
    # 3 fichas, una por jugada: gana quien hace la tercera, el primer jugador.
    assert (result.kind, result.winner_id) == (OutcomeKind.COMPLETED, "slow-1")
    assert 1.4 <= elapsed < 3
