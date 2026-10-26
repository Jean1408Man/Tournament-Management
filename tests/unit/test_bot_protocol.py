import io
import json
import subprocess
import sys
from pathlib import Path

import pytest

from tournament_management.game.protocol import (
    MAX_MESSAGE_BYTES,
    encode,
    read_message,
    write_message,
)

ROOT = Path(__file__).resolve().parents[2]


def test_jsonl_roundtrip_flushes_and_compacts():
    stream = io.StringIO()
    write_message(stream, {"type": "move", "pile": 1, "count": 2})
    assert read_message(io.StringIO(stream.getvalue())) == {"type": "move", "pile": 1, "count": 2}
    assert stream.getvalue().endswith("\n")


def test_protocol_rejects_oversized_message():
    with pytest.raises(ValueError, match="demasiado grande"):
        encode({"type": "error", "message": "x" * MAX_MESSAGE_BYTES})


def run_bot(path, messages):
    return subprocess.run(
        [sys.executable, str(path)],
        input="".join(json.dumps(message) + "\n" for message in messages),
        capture_output=True,
        text=True,
        timeout=3,
    )


def test_optimal_bot_returns_ready_and_winning_move():
    result = run_bot(
        ROOT / "bots/optimal.py",
        [
            {"type": "hello", "protocol": "nim-jsonl", "version": 1},
            {"type": "turn", "turn": 0, "piles": [1, 2], "your_turn": True},
        ],
    )
    lines = [json.loads(line) for line in result.stdout.splitlines()]
    assert lines[0] == {"type": "ready", "protocol": "nim-jsonl", "version": 1}
    assert lines[1] == {"type": "move", "pile": 1, "count": 1}


def test_random_bot_seed_is_reproducible():
    messages = [
        {"type": "hello", "protocol": "nim-jsonl", "version": 1},
        {"type": "turn", "turn": 0, "piles": [3, 4], "your_turn": True},
    ]
    first = run_bot(ROOT / "bots/random_bot.py", messages)
    second = subprocess.run(
        [sys.executable, str(ROOT / "bots/random_bot.py"), "--seed", "7"],
        input="".join(json.dumps(message) + "\n" for message in messages),
        capture_output=True,
        text=True,
        timeout=3,
    )
    initial = run_bot(ROOT / "bots/random_bot.py", messages)
    assert first.stdout == initial.stdout
    assert second.returncode == 0
