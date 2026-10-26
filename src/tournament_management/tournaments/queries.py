"""Consultas consistentes: cada lectura toma brevemente el bloqueo del torneo.

Así un cliente no recibe una clasificación posterior con contadores anteriores.
Las conexiones se cierran antes de esperar o de enviar un evento por la red.
"""

from collections.abc import Callable

from tournament_management.shared.errors import ConflictError
from tournament_management.shared.unit_of_work import UnitOfWork
from tournament_management.tournaments.bracket import elimination_bracket
from tournament_management.tournaments.model import TournamentSpec


class TournamentQueries:
    def __init__(self, work_factory: Callable[[], UnitOfWork]) -> None:
        self.work_factory = work_factory

    def state(self, tournament_id: str) -> str:
        with self.work_factory() as work:
            return work.tournaments.state(tournament_id).value

    def tournament(self, tournament_id: str) -> dict:
        with self.work_factory() as work:
            row = work.tournaments.lock(tournament_id)
            definition = row["definition"]
            if definition is None:
                raise ConflictError("El torneo antiguo no tiene definición consultable")
            summary = work.matches.summary(tournament_id)
            bracket = elimination_bracket(
                TournamentSpec.from_dict(definition),
                work.matches.bracket_rows(tournament_id),
            )
        return {
            "tournament_id": tournament_id,
            "state": row["state"],
            "format": definition["format_name"],
            "competitor_ids": definition["competitor_ids"],
            "statistics": summary,
            "bracket": bracket,
            "champion": row["progress"].get("champion"),
            "standings": [
                {"competitor_id": identifier, "wins": wins}
                for identifier, wins in row["progress"].get("standings", [])
            ],
        }

    def matches(self, tournament_id: str, *, offset: int, limit: int) -> dict:
        with self.work_factory() as work:
            work.tournaments.lock(tournament_id)
            total = work.matches.summary(tournament_id)["matches_total"]
            items = work.matches.page(tournament_id, offset=offset, limit=limit)
        return {"items": items, "total": total, "offset": offset, "limit": limit}

    def events(self, tournament_id: str, after: int, *, limit: int = 100) -> tuple:
        with self.work_factory() as work:
            row = work.tournaments.lock(tournament_id)
            if after > work.events.latest_sequence(tournament_id):
                raise ConflictError("El cursor está por delante del historial confirmado")
            events = work.events.list(tournament_id, after, limit=limit)
        return row["state"], events
