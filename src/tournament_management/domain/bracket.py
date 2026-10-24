"""Vista del cuadro de eliminación a partir de las mismas reglas deportivas."""

from tournament_management.domain.formats.elimination import Elimination
from tournament_management.domain.persistent_tournament import TournamentSpec


def elimination_bracket(spec: TournamentSpec, matches: list[dict]) -> list[dict]:
    if spec.format_name != "elimination":
        return []
    tournament = Elimination(spec.competitor_ids)
    by_id = {match["match_id"]: match for match in matches}
    entries = []
    while tournament.status == "running":
        pending = tournament.pending()
        complete_round = True
        for pairing in pending:
            match_id = spec.match(pairing.match_id, pairing.competitors).match_id
            row = by_id[match_id]
            entries.append(
                {
                    "pairing_id": pairing.match_id,
                    "round_number": int(pairing.match_id.split("-")[0][1:]),
                    "match_id": match_id,
                    "competitor_ids": list(pairing.competitors),
                    "state": row["state"],
                    "winner_id": row["winner_id"],
                }
            )
            if row["state"] == "completed":
                tournament.record(pairing.match_id, row["winner_id"])
            else:
                complete_round = False
        if not complete_round:
            break
    return entries
