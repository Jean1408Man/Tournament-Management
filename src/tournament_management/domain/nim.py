"""Reglas puras de Nim normal, sin dependencias de transporte o persistencia."""

from __future__ import annotations

import random
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Position:
    piles: tuple[int, ...]
    player: int = 0

    def __post_init__(self) -> None:
        if type(self.piles) is not tuple or not self.piles:
            raise ValueError("La posición requiere al menos un montón")
        if any(type(pile) is not int or pile < 0 for pile in self.piles):
            raise ValueError("Los montones deben ser enteros no negativos")
        if self.player not in (0, 1):
            raise ValueError("El jugador debe ser 0 o 1")


@dataclass(frozen=True, slots=True)
class Move:
    pile: int
    count: int

    def __post_init__(self) -> None:
        if type(self.pile) is not int or self.pile < 0:
            raise ValueError("Índice de montón inválido")
        if type(self.count) is not int or self.count <= 0:
            raise ValueError("La retirada debe ser positiva")


def validate_move(position: Position, move: Move) -> None:
    if move.pile >= len(position.piles):
        raise ValueError("Índice de montón fuera de rango")
    if move.count > position.piles[move.pile]:
        raise ValueError("No se pueden retirar más fichas de las disponibles")


def apply_move(position: Position, move: Move) -> Position:
    validate_move(position, move)
    piles = list(position.piles)
    piles[move.pile] -= move.count
    return Position(tuple(piles), player=1 - position.player)


def is_terminal(position: Position) -> bool:
    return not any(position.piles)


def winner(position: Position) -> int | None:
    """Ganador en Nim normal; ``None`` mientras la partida siga abierta."""
    return None if not is_terminal(position) else 1 - position.player


def initial_position(piles: tuple[int, ...], first_player: int = 0) -> Position:
    if not piles or any(type(pile) is not int or pile <= 0 for pile in piles):
        raise ValueError("La posición inicial requiere montones positivos")
    return Position(tuple(piles), player=first_player)


def generate_position(
    seed: int,
    *,
    pile_count: int = 3,
    min_pile: int = 1,
    max_pile: int = 7,
    first_player: int = 0,
) -> Position:
    """Genera una posición inicial reproducible con un generador local."""
    if type(seed) is not int:
        raise ValueError("La semilla debe ser un entero")
    if type(pile_count) is not int or pile_count <= 0:
        raise ValueError("El número de montones debe ser positivo")
    if type(min_pile) is not int or type(max_pile) is not int or min_pile <= 0:
        raise ValueError("Los límites de los montones no son válidos")
    if min_pile > max_pile:
        raise ValueError("El mínimo no puede superar al máximo")
    rng = random.Random(seed)
    return initial_position(
        tuple(rng.randint(min_pile, max_pile) for _ in range(pile_count)), first_player
    )


__all__ = [
    "Move", "Position", "apply_move", "generate_position", "initial_position",
    "is_terminal", "validate_move", "winner",
]
