"""Codificación y cursores SSE: el ID combina torneo y secuencia confirmada."""

from tournament_management.api.errors import ApiError
from tournament_management.shared.messages import DomainEvent


def parse_cursor(tournament_id: str, last_event_id: str | None, after: int) -> int:
    if last_event_id is None:
        return after
    owner, separator, sequence = last_event_id.rpartition(":")
    if (
        after != 0
        or not separator
        or owner != tournament_id
        or not sequence.isascii()
        or not sequence.isdecimal()
        or len(sequence) > 18
    ):
        raise ApiError(400, "invalid_event_cursor", "El cursor no pertenece a este torneo")
    return int(sequence)


def encode(event: DomainEvent) -> str:
    return (
        f"id: {event.tournament_id}:{event.sequence}\n"
        f"event: {event.event_type}\ndata: {event.to_json()}\n\n"
    )
