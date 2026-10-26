"""Árbitro local de Nim: procesos, protocolo y resultado deportivo."""

from __future__ import annotations

import threading
import time

from tournament_management.game.limits import Limits
from tournament_management.game.nim import Move, apply_move, initial_position, is_terminal
from tournament_management.game.process_io import BotFault, BotPipes
from tournament_management.shared.messages import AttemptOutcome, AttemptSpec, OutcomeKind


def _outcome(
    attempt: AttemptSpec, worker_id: str, kind: OutcomeKind,
    winner_id: str, reason: str,
) -> AttemptOutcome:
    return AttemptOutcome(
        match_id=attempt.match.match_id, attempt_id=attempt.attempt_id,
        generation=attempt.generation, worker_id=worker_id,
        kind=kind, winner_id=winner_id, reason=reason,
    )


def run_match(
    attempt: AttemptSpec,
    commands: tuple[tuple[str, ...], tuple[str, ...]],
    *,
    worker_id: str = "local",
    limits: Limits | None = None,
    cancel: threading.Event | None = None,
) -> AttemptOutcome:
    """Juega una partida y limpia ambos grupos de procesos en cualquier salida."""
    limits = limits or Limits(
        turn_timeout_ms=attempt.turn_timeout_ms,
        match_timeout_ms=attempt.match_timeout_ms,
        memory_limit_bytes=attempt.memory_limit_bytes,
        output_limit_bytes=attempt.output_limit_bytes,
    )
    pipes = BotPipes(limits.memory_limit_bytes, limits.output_limit_bytes)
    match_deadline = time.monotonic() + limits.match_timeout_ms / 1000
    try:
        if len(commands) != 2:
            raise ValueError("Se requieren dos comandos de bot")
        for player, command in enumerate(commands):
            if cancel is not None and cancel.is_set():
                raise InterruptedError("cancelled")
            pipes.start(command, player)
        for player in (0, 1):
            pipes.send(
                player, {"type": "hello", "protocol": "nim-jsonl", "version": 1},
                match_deadline, cancel,
            )
        faults: list[BotFault] = []
        for player in (0, 1):
            try:
                ready = pipes.read(
                    player, min(match_deadline, time.monotonic() + limits.turn_timeout_ms / 1000),
                    cancel,
                )
                if ready != {"type": "ready", "protocol": "nim-jsonl", "version": 1}:
                    raise BotFault(player, "Respuesta ready inválida")
            except BotFault as exc:
                faults.append(exc)
        if len({fault.player for fault in faults}) == 2:
            return _outcome(attempt, worker_id, OutcomeKind.NO_WINNER, "", "both bots failed")
        if faults:
            fault = faults[0]
            return _outcome(
                attempt, worker_id, OutcomeKind.BOT_FAILURE,
                attempt.match.competitor_ids[1 - fault.player], str(fault),
            )

        position = initial_position(attempt.match.initial_piles, attempt.match.first_player)
        for player in (0, 1):
            pipes.send(player, {
                "type": "start", "game_id": attempt.match.match_id,
                "player": player, "piles": list(position.piles),
                "your_turn": player == position.player,
            }, match_deadline, cancel)
        turn = 0
        while not is_terminal(position):
            player = position.player
            deadline = min(match_deadline, time.monotonic() + limits.turn_timeout_ms / 1000)
            pipes.send(player, {
                "type": "turn", "turn": turn, "piles": list(position.piles),
                "your_turn": True,
            }, deadline, cancel)
            message = pipes.read(player, deadline, cancel)
            if set(message) != {"type", "pile", "count"} or message["type"] != "move":
                raise BotFault(player, "Movimiento inválido")
            try:
                position = apply_move(position, Move(message["pile"], message["count"]))
            except (ValueError, TypeError) as exc:
                raise BotFault(player, "Movimiento ilegal") from exc
            turn += 1
        winner = 1 - position.player
        for player in (0, 1):
            try:
                pipes.send(player, {
                    "type": "game_over", "result": "win" if player == winner else "loss",
                    "winner": winner, "reason": "last_move",
                }, match_deadline, cancel)
            except BotFault:
                pass  # La partida ya terminó con un ganador válido.
        return _outcome(
            attempt, worker_id, OutcomeKind.COMPLETED,
            attempt.match.competitor_ids[winner], "last_move",
        )
    except BotFault as exc:
        return _outcome(
            attempt, worker_id, OutcomeKind.BOT_FAILURE,
            attempt.match.competitor_ids[1 - exc.player], str(exc),
        )
    except (OSError, InterruptedError, ValueError) as exc:
        return _outcome(
            attempt, worker_id, OutcomeKind.INFRASTRUCTURE_FAILURE, "", str(exc),
        )
    finally:
        pipes.close()
