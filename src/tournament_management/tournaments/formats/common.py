"""Contrato común para formatos deportivos independientes de la ejecución."""

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True, slots=True)
class Pairing:
    match_id: str
    competitors: tuple[str, str]


class TournamentFormat(Protocol):
    @property
    def status(self) -> str: ...

    def pending(self) -> tuple[Pairing, ...]: ...

    def record(self, match_id: str, winner_id: str | None) -> None: ...


def validate_competitors(competitors: tuple[str, ...]) -> None:
    if len(competitors) < 2 or any(not isinstance(c, str) or not c.strip() for c in competitors):
        raise ValueError("Se requieren al menos dos competidores con ID no vacío")
    if len(set(competitors)) != len(competitors):
        raise ValueError("Los competidores deben ser distintos")
