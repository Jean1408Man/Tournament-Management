"""Transiciones puras; las precondiciones de negocio se comprueban en servicios."""

from enum import StrEnum


class TournamentState(StrEnum):
    CREATED = "created"
    RUNNING = "running"
    COMPLETED = "completed"
    BLOCKED = "blocked"
    CANCELLED = "cancelled"


class MatchState(StrEnum):
    WAITING = "waiting"
    READY = "ready"
    RUNNING = "running"
    COMPLETED = "completed"
    BLOCKED = "blocked"
    CANCELLED = "cancelled"


class AttemptState(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    ACCEPTED = "accepted"
    FAILED = "failed"
    LOST = "lost"
    EXPIRED = "expired"
    DISCARDED = "discarded"
    CANCELLED = "cancelled"


TRANSITIONS = {
    TournamentState: {
        "created": {"running", "cancelled"},
        "running": {"completed", "blocked", "cancelled"},
        "blocked": {"cancelled"},
    },
    MatchState: {
        "waiting": {"ready", "blocked", "cancelled"},
        "ready": {"running", "blocked", "cancelled"},
        "running": {"ready", "completed", "blocked", "cancelled"},
        "blocked": {"cancelled"},
    },
    AttemptState: {
        "queued": {"running", "lost", "expired", "cancelled"},
        "running": {"accepted", "failed", "lost", "expired", "discarded", "cancelled"},
    },
}


def transition[S: StrEnum](current: S, target: S) -> S:
    """Rechaza incluso repeticiones: la idempotencia pertenece al servicio."""
    if type(current) is not type(target) or type(current) not in TRANSITIONS:
        raise ValueError("Estados de entidades distintas o desconocidas")
    if target.value not in TRANSITIONS[type(current)].get(current.value, set()):
        raise ValueError(f"Transición inválida: {current} -> {target}")
    return target
