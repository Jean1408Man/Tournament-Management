"""Reconstrucción desde commits, antes de admitir HTTP o ejecutar intentos nuevos.

Presupone un único coordinador. Cada torneo se recupera en una transacción
independiente: otro corte puede repetir el recorrido sin duplicar efectos.
"""

from collections.abc import Callable

from tournament_management.events.durable import append_event
from tournament_management.shared.states import TournamentState
from tournament_management.shared.unit_of_work import UnitOfWork


class StartupRecovery:
    def __init__(self, work_factory: Callable[[], UnitOfWork], *, max_retries: int) -> None:
        self.work_factory = work_factory
        self.max_attempts = 1 + max_retries

    def recover(self) -> tuple[str, ...]:
        with self.work_factory() as work:
            identifiers = work.tournaments.recoverable_ids()
        return tuple(identifier for identifier in identifiers if self._recover_one(identifier))

    def _recover_one(self, tournament_id: str) -> bool:
        with self.work_factory() as work:
            row = work.tournaments.lock(tournament_id)
            if row["state"] != TournamentState.RUNNING.value:
                # Cancelar puede ganar después de enumerar los candidatos.
                return False
            rows = work.matches.recovery_rows(tournament_id)
            exhausted = False
            for match in rows:
                attempt_id = work.matches.lose_current(match["match_id"])
                if attempt_id is not None:
                    append_event(
                        work,
                        tournament_id,
                        "attempt.lost",
                        match["match_id"],
                        attempt_id,
                        detail="coordinator_restart",
                    )
                # El historial confirmado consume presupuesto incluso si el
                # proceso murió entre emitir y enviar, o entre fallo y reemplazo.
                exhausted |= match["generation"] >= self.max_attempts
            if exhausted:
                work.matches.block_pending(tournament_id)
                work.tournaments.set_state(tournament_id, TournamentState.BLOCKED)
                append_event(
                    work,
                    tournament_id,
                    "tournament.blocked",
                    detail="retry_budget_exhausted_on_recovery",
                )
            work.commit()
        return bool(rows) and not exhausted
