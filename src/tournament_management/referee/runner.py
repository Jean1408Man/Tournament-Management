"""Árbitro local de una partida de Nim entre dos procesos."""

from __future__ import annotations

import json
import os
import selectors
import signal
import subprocess
import time
from dataclasses import dataclass

from tournament_management.domain.messages import AttemptOutcome, AttemptSpec, OutcomeKind
from tournament_management.referee.limits import Limits
from tournament_management.referee.protocol import encode


@dataclass
class _Bot:
    process: subprocess.Popen[str]
    command: tuple[str, ...]
    output_bytes: int = 0


def _preexec(memory_limit: int):
    def configure() -> None:
        import resource

        resource.setrlimit(resource.RLIMIT_AS, (memory_limit, memory_limit))
        os.setsid()

    return configure


def _terminate(bots: list[_Bot]) -> None:
    for bot in bots:
        if bot.process.poll() is None:
            try:
                os.killpg(bot.process.pid, signal.SIGKILL)
            except ProcessLookupError:
                bot.process.kill()
    for bot in bots:
        try:
            bot.process.communicate(timeout=1)
        except subprocess.TimeoutExpired:
            bot.process.kill()
            bot.process.communicate()


def _read_line(bot: _Bot, deadline: float, output_limit: int) -> dict[str, object]:
    selector = selectors.DefaultSelector()
    try:
        selector.register(bot.process.stdout, selectors.EVENT_READ)
        remaining = max(0.0, deadline - time.monotonic())
        if not selector.select(remaining):
            raise TimeoutError("timeout")
        line = bot.process.stdout.readline()
    finally:
        selector.close()
    if not line:
        raise ValueError("El bot terminó prematuramente")
    bot.output_bytes += len(line.encode())
    if bot.output_bytes > output_limit:
        raise ValueError("Salida del bot demasiado grande")
    try:
        message = json.loads(line)
    except json.JSONDecodeError as exc:
        raise ValueError("Salida del bot no es JSON") from exc
    if type(message) is not dict:
        raise ValueError("El mensaje del bot debe ser un objeto")
    return message


def _send(bot: _Bot, message: dict[str, object]) -> None:
    bot.process.stdin.write(encode(message))
    bot.process.stdin.flush()


def run_match(
    attempt: AttemptSpec,
    commands: tuple[tuple[str, ...], tuple[str, ...]],
    *,
    worker_id: str = "local",
    limits: Limits | None = None,
) -> AttemptOutcome:
    """Ejecuta y arbitra una partida, clasificando fallos deportivos."""
    limits = limits or Limits(
        turn_timeout_ms=attempt.turn_timeout_ms,
        match_timeout_ms=attempt.match_timeout_ms,
        memory_limit_bytes=attempt.memory_limit_bytes,
        output_limit_bytes=attempt.output_limit_bytes,
    )
    bots: list[_Bot] = []
    start = time.monotonic()
    try:
        for command in commands:
            process = subprocess.Popen(
                list(command), stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                stderr=subprocess.PIPE, text=True, bufsize=1,
                start_new_session=True,
            )
            bots.append(_Bot(process, command))
        for bot in bots:
            _send(bot, {"type": "hello", "protocol": "nim-jsonl", "version": 1})
            ready = _read_line(
                bot, start + limits.turn_timeout_ms / 1000, limits.output_limit_bytes
            )
            if ready != {"type": "ready", "protocol": "nim-jsonl", "version": 1}:
                raise ValueError("Respuesta ready inválida")
        position = list(attempt.match.initial_piles)
        player = attempt.match.first_player
        for index, bot in enumerate(bots):
            _send(bot, {
                "type": "start", "game_id": attempt.match.match_id,
                "player": index, "piles": position, "your_turn": index == player,
            })
        turn = 0
        while any(position):
            if time.monotonic() - start > limits.match_timeout_ms / 1000:
                raise TimeoutError("timeout de partida")
            bot = bots[player]
            _send(bot, {"type": "turn", "turn": turn, "piles": position, "your_turn": True})
            move = _read_line(
                bot, time.monotonic() + limits.turn_timeout_ms / 1000, limits.output_limit_bytes
            )
            if set(move) != {"type", "pile", "count"} or move["type"] != "move":
                raise ValueError("Movimiento inválido")
            pile, count = move["pile"], move["count"]
            if type(pile) is not int or type(count) is not int or not 0 <= pile < len(position):
                raise ValueError("Movimiento inválido")
            if count <= 0 or count > position[pile]:
                raise ValueError("Movimiento ilegal")
            position[pile] -= count
            turn += 1
            player = 1 - player
        for bot in bots:
            _send(
                bot,
                {"type": "game_over", "result": "win", "winner": 1 - player, "reason": "last_move"},
            )
        return AttemptOutcome(
            match_id=attempt.match.match_id, attempt_id=attempt.attempt_id,
            generation=attempt.generation, worker_id=worker_id,
            kind=OutcomeKind.COMPLETED,
            winner_id=attempt.match.competitor_ids[1 - player], reason="last_move",
        )
    except TimeoutError as exc:
        winner = (
            attempt.match.competitor_ids[1 - player]
            if "player" in locals()
            else attempt.match.competitor_ids[1]
        )
        return AttemptOutcome(
            match_id=attempt.match.match_id, attempt_id=attempt.attempt_id,
            generation=attempt.generation, worker_id=worker_id,
            kind=OutcomeKind.BOT_FAILURE, winner_id=winner, reason=str(exc),
        )
    except (OSError, ValueError) as exc:
        winner = (
            attempt.match.competitor_ids[1 - player]
            if "player" in locals()
            else attempt.match.competitor_ids[1]
        )
        return AttemptOutcome(
            match_id=attempt.match.match_id, attempt_id=attempt.attempt_id,
            generation=attempt.generation, worker_id=worker_id,
            kind=OutcomeKind.BOT_FAILURE, winner_id=winner, reason=str(exc),
        )
    finally:
        _terminate(bots)
