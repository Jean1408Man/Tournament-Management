"""Una partida por pareja y tabla ordenada de forma determinista."""

from itertools import combinations

from tournament_management.tournaments.formats.common import Pairing, validate_competitors


class RoundRobin:
    def __init__(self, competitors: tuple[str, ...]):
        validate_competitors(competitors)
        self.competitors = competitors
        self._pending = {
            f"m{index}": Pairing(f"m{index}", pair)
            for index, pair in enumerate(combinations(competitors, 2), 1)
        }
        self._wins = dict.fromkeys(competitors, 0)
        self._status = "running"

    @property
    def status(self) -> str:
        return self._status

    @property
    def standings(self) -> tuple[tuple[str, int], ...]:
        order = {competitor: i for i, competitor in enumerate(self.competitors)}
        return tuple(sorted(self._wins.items(), key=lambda row: (-row[1], order[row[0]])))

    def pending(self) -> tuple[Pairing, ...]:
        return tuple(self._pending.values()) if self._status == "running" else ()

    def record(self, match_id: str, winner_id: str | None) -> None:
        if self._status != "running" or match_id not in self._pending:
            raise ValueError("Partida no pendiente o resultado duplicado")
        pairing = self._pending[match_id]
        if winner_id is not None and winner_id not in pairing.competitors:
            raise ValueError("El ganador no participa en la partida")
        if winner_id is None:
            self._status = "blocked"
            self._pending.clear()
            return
        self._wins[winner_id] += 1
        del self._pending[match_id]
        if not self._pending:
            self._status = "completed"
