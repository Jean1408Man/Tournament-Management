"""Añade eventos a la transacción del caso de uso, sin publicarlos antes del commit."""

from datetime import UTC, datetime
from uuid import uuid4

from tournament_management.shared.messages import DomainEvent
from tournament_management.shared.unit_of_work import UnitOfWork


def append_event(
    work: UnitOfWork,
    tournament_id: str,
    event_type: str,
    match_id: str = "",
    attempt_id: str = "",
    *,
    detail: str | None = None,
) -> DomainEvent:
    event = DomainEvent(
        event_id=str(uuid4()),
        tournament_id=tournament_id,
        match_id=match_id,
        attempt_id=attempt_id,
        event_type=event_type,
        sequence=work.events.next_sequence(tournament_id),
        occurred_at=datetime.now(UTC).isoformat(),
        detail=event_type if detail is None else detail,
    )
    work.events.append(event)
    return event
