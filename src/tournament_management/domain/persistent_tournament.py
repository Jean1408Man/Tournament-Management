"""Definición durable y reconstrucción determinista a partir de resultados.

Se reutilizan los formatos deportivos de la etapa 2: la base no contiene otra
implementación de las reglas. El orden de inscripción y las semillas forman
parte de la definición inmutable, de modo que releerla produce el mismo cuadro.
"""

import hashlib
from dataclasses import dataclass
from uuid import NAMESPACE_URL, uuid5

from tournament_management.domain.formats.elimination import Elimination
from tournament_management.domain.formats.round_robin import RoundRobin
from tournament_management.domain.messages import AttemptOutcome, MatchSpec, Message, require_id
from tournament_management.domain.tournaments import TournamentFormat, validate_competitors


@dataclass(frozen=True, kw_only=True)
class TournamentSpec(Message):
    tournament_id: str
    format_name: str
    competitor_ids: tuple[str, ...]
    artifact_hashes: tuple[str, ...]
    initial_piles: tuple[int, ...]
    first_player: int
    seed: int

    def __post_init__(self) -> None:
        super().__post_init__()
        require_id(self.tournament_id)
        validate_competitors(self.competitor_ids)
        if self.format_name not in {"elimination", "round_robin"}:
            raise ValueError("Formato desconocido")
        if len(self.artifact_hashes) != len(self.competitor_ids):
            raise ValueError("Cada participante requiere un hash")
        # MatchSpec ya valida posición, turno y hashes: conservar una sola regla.
        for index in range(1, len(self.competitor_ids)):
            MatchSpec(
                tournament_id=self.tournament_id,
                match_id="validation",
                competitor_ids=(self.competitor_ids[0], self.competitor_ids[index]),
                artifact_hashes=(self.artifact_hashes[0], self.artifact_hashes[index]),
                initial_piles=self.initial_piles,
                first_player=self.first_player,
                seed=self.seed,
            )

    def match(self, pairing_id: str, competitors: tuple[str, str]) -> MatchSpec:
        hashes = dict(zip(self.competitor_ids, self.artifact_hashes, strict=True))
        # Los nombres r1-m1/m1 de los formatos se repiten entre torneos.
        # Un UUID derivado evita colisiones sin depender del orden de ejecución.
        identifier = str(uuid5(NAMESPACE_URL, f"tm:{self.tournament_id}:{pairing_id}"))
        seed = int.from_bytes(
            hashlib.sha256(f"{self.seed}:{pairing_id}".encode()).digest()[:8], "big"
        )
        return MatchSpec(
            tournament_id=self.tournament_id,
            match_id=identifier,
            competitor_ids=competitors,
            artifact_hashes=tuple(hashes[c] for c in competitors),
            initial_piles=self.initial_piles,
            first_player=self.first_player,
            seed=seed,
        )


def rebuild(spec: TournamentSpec, results: tuple[AttemptOutcome, ...]) -> TournamentFormat:
    """Reproduce únicamente resultados aceptados, en orden de confirmación."""
    tournament = (
        Elimination(spec.competitor_ids)
        if spec.format_name == "elimination"
        else RoundRobin(spec.competitor_ids)
    )
    for outcome in results:
        pairing = next(
            (
                pair
                for pair in tournament.pending()
                if spec.match(pair.match_id, pair.competitors).match_id == outcome.match_id
            ),
            None,
        )
        if pairing is None:
            raise ValueError("Historial incompatible con el cuadro del torneo")
        tournament.record(pairing.match_id, outcome.winner_id)
    return tournament


def progress(tournament: TournamentFormat) -> dict:
    """Proyección consultable; el historial sigue siendo la fuente del avance."""
    if isinstance(tournament, Elimination):
        return {"champion": tournament.champion}
    return {"standings": list(tournament.standings)}
