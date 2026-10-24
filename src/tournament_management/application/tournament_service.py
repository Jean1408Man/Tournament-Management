"""Simulación local de formatos con un ejecutor de partidas inyectado."""

from __future__ import annotations

import hashlib
from collections.abc import Callable
from dataclasses import dataclass, replace

from tournament_management.domain.formats.elimination import Elimination
from tournament_management.domain.formats.round_robin import RoundRobin
from tournament_management.domain.messages import (
    AttemptOutcome,
    AttemptSpec,
    MatchSpec,
    OutcomeKind,
)
from tournament_management.domain.tournaments import Pairing, TournamentFormat, validate_competitors


@dataclass(frozen=True, slots=True)
class Competitor:
    competitor_id: str
    artifact_hash: str


def run_tournament(
    tournament_id: str,
    format_name: str,
    competitors: tuple[Competitor, ...],
    initial_piles: tuple[int, ...],
    first_player: int,
    seed: int,
    limits: tuple[int, int, int, int],
    execute: Callable[[AttemptSpec, Pairing], AttemptOutcome],
) -> dict:
    """Produce partidas y consume resultados hasta completar o bloquear el formato."""
    validate_competitors(tuple(c.competitor_id for c in competitors))
    if format_name == "elimination":
        tournament: TournamentFormat = Elimination(tuple(c.competitor_id for c in competitors))
    elif format_name == "round_robin":
        tournament = RoundRobin(tuple(c.competitor_id for c in competitors))
    else:
        raise ValueError("Formato desconocido")
    hashes = {c.competitor_id: c.artifact_hash for c in competitors}
    results: list[dict] = []
    while tournament.status == "running":
        pending = tournament.pending()
        if not pending:
            raise RuntimeError("Torneo activo sin partidas pendientes")
        for pairing in pending:
            match_seed = int.from_bytes(
                hashlib.sha256(f"{seed}:{pairing.match_id}".encode()).digest()[:8], "big"
            )
            match = MatchSpec(
                tournament_id=tournament_id, match_id=pairing.match_id,
                competitor_ids=pairing.competitors,
                artifact_hashes=tuple(hashes[c] for c in pairing.competitors),
                initial_piles=initial_piles, first_player=first_player, seed=match_seed,
            )
            attempt = AttemptSpec(
                attempt_id=f"{pairing.match_id}-a1", generation=1, match=match,
                turn_timeout_ms=limits[0], match_timeout_ms=limits[1],
                memory_limit_bytes=limits[2], output_limit_bytes=limits[3],
            )
            outcome = execute(attempt, pairing)
            # El scheduler puede devolver un reintento distinto del inicial.
            if outcome.match_id != match.match_id or outcome.generation < attempt.generation:
                raise ValueError("El resultado no corresponde a la partida")
            outcome.validate_for(replace(
                attempt, attempt_id=outcome.attempt_id, generation=outcome.generation,
            ))
            results.append({
                "match_id": pairing.match_id, "competitors": pairing.competitors,
                "kind": outcome.kind.value, "winner_id": outcome.winner_id or None,
                "reason": outcome.reason,
            })
            winner = (
                outcome.winner_id
                if outcome.kind in (OutcomeKind.COMPLETED, OutcomeKind.BOT_FAILURE)
                else None
            )
            tournament.record(pairing.match_id, winner)
            if tournament.status == "blocked":
                break
    report = {
        "tournament_id": tournament_id, "format": format_name,
        "status": tournament.status, "matches": results, "played": len(results),
    }
    if isinstance(tournament, Elimination):
        report["champion"] = tournament.champion
    else:
        report["standings"] = [
            {"competitor_id": competitor_id, "wins": wins}
            for competitor_id, wins in tournament.standings
        ]
    return report
