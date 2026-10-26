"""Eliminación directa por rondas, con byes y dependencias explícitas."""

from tournament_management.tournaments.formats.common import Pairing, validate_competitors


class Elimination:
    def __init__(self, competitors: tuple[str, ...]):
        validate_competitors(competitors)
        self.competitors = competitors
        size = 1 << (len(competitors) - 1).bit_length()
        self._slots: tuple[str | None, ...] = (
            *competitors, *(None for _ in range(size - len(competitors)))
        )
        self._round = 1
        self._pending: dict[str, Pairing] = {}
        self._winners: dict[int, str | None] = {}
        self._status = "running"
        self._champion: str | None = None
        self._prepare_round()

    @property
    def status(self) -> str:
        return self._status

    @property
    def champion(self) -> str | None:
        return self._champion

    def pending(self) -> tuple[Pairing, ...]:
        return tuple(self._pending.values()) if self._status == "running" else ()

    def _prepare_round(self) -> None:
        self._pending = {}
        self._winners = {}
        for slot in range(len(self._slots) // 2):
            left, right = self._slots[2 * slot : 2 * slot + 2]
            if left is None or right is None:
                self._winners[slot] = left or right
            else:
                match_id = f"r{self._round}-m{slot + 1}"
                self._pending[match_id] = Pairing(match_id, (left, right))
        self._advance_if_ready()

    def _advance_if_ready(self) -> None:
        if self._pending or self._status != "running":
            return
        slots = tuple(self._winners[i] for i in range(len(self._slots) // 2))
        if len(slots) == 1:
            self._champion = slots[0]
            self._status = "completed" if self._champion else "blocked"
        else:
            self._slots = slots
            self._round += 1
            self._prepare_round()

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
        slot = int(match_id.split("-m")[1]) - 1
        self._winners[slot] = winner_id
        del self._pending[match_id]
        self._advance_if_ready()
