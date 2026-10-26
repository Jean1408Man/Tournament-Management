"""Mensajes inmutables y versionados, sin dependencias de transporte."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from enum import StrEnum
from typing import Self, get_args, get_origin, get_type_hints


def _decode(value: object, expected: type) -> object:
    if get_origin(expected) is tuple:
        if not isinstance(value, list | tuple):
            raise ValueError("Se esperaba una secuencia")
        return tuple(_decode(item, get_args(expected)[0]) for item in value)
    if isinstance(expected, type) and issubclass(expected, Message):
        if not isinstance(value, dict):
            raise ValueError("Se esperaba un mensaje")
        return expected.from_dict(value)
    if isinstance(expected, type) and issubclass(expected, StrEnum):
        return expected(value)
    if type(value) is not expected:
        raise ValueError(f"Tipo inválido: se esperaba {expected}")
    return value


@dataclass(frozen=True, kw_only=True)
class Message:
    schema_version: int = 1

    def __post_init__(self) -> None:
        if type(self.schema_version) is not int or self.schema_version != 1:
            raise ValueError("Versión de mensaje no soportada")
        for name, expected in get_type_hints(type(self)).items():
            value = getattr(self, name)
            if isinstance(expected, type) and issubclass(expected, Message):
                if type(value) is not expected:
                    raise ValueError(f"Mensaje anidado inválido: {name}")
                continue
            if get_origin(expected) is tuple and type(value) is not tuple:
                raise ValueError("Las secuencias internas deben ser tuplas inmutables")
            if isinstance(expected, type) and issubclass(expected, StrEnum):
                if type(value) is not expected:
                    raise ValueError(f"Se requiere el enum {expected.__name__}")
            _decode(value, expected)

    def to_json(self) -> str:
        return json.dumps(asdict(self), sort_keys=True, allow_nan=False)

    @classmethod
    def from_dict(cls, data: dict) -> Self:
        fields = get_type_hints(cls)
        if set(data) != set(fields):
            raise ValueError("Campos ausentes o desconocidos")
        return cls(**{key: _decode(value, fields[key]) for key, value in data.items()})

    @classmethod
    def from_json(cls, raw: str) -> Self:
        data = json.loads(raw)
        if not isinstance(data, dict):
            raise ValueError("El mensaje debe ser un objeto")
        return cls.from_dict(data)


def require_id(value: str) -> None:
    if not value.strip():
        raise ValueError("Identificador vacío")


@dataclass(frozen=True, kw_only=True)
class MatchSpec(Message):
    tournament_id: str
    match_id: str
    competitor_ids: tuple[str, ...]
    artifact_hashes: tuple[str, ...]
    initial_piles: tuple[int, ...]
    first_player: int
    seed: int

    def __post_init__(self) -> None:
        super().__post_init__()
        require_id(self.tournament_id)
        require_id(self.match_id)
        if len(self.competitor_ids) != 2 or len(set(self.competitor_ids)) != 2:
            raise ValueError("Se requieren dos competidores distintos")
        for competitor in self.competitor_ids:
            require_id(competitor)
        if len(self.artifact_hashes) != 2 or any(
            len(h) != 64 or any(c not in "0123456789abcdef" for c in h)
            for h in self.artifact_hashes
        ):
            raise ValueError("Se requieren dos hashes SHA-256 hexadecimales")
        if not self.initial_piles or any(n <= 0 for n in self.initial_piles):
            raise ValueError("La posición inicial requiere montones positivos")
        if self.first_player not in (0, 1):
            raise ValueError("Primer jugador fuera de rango")


@dataclass(frozen=True, kw_only=True)
class AttemptSpec(Message):
    attempt_id: str
    generation: int
    match: MatchSpec
    turn_timeout_ms: int
    match_timeout_ms: int
    memory_limit_bytes: int
    output_limit_bytes: int

    def __post_init__(self) -> None:
        super().__post_init__()
        require_id(self.attempt_id)
        if (
            min(
                self.generation,
                self.turn_timeout_ms,
                self.match_timeout_ms,
                self.memory_limit_bytes,
                self.output_limit_bytes,
            )
            <= 0
        ):
            raise ValueError("Generación y límites deben ser positivos")
        if self.match_timeout_ms < self.turn_timeout_ms:
            raise ValueError("El límite de partida no puede ser menor que el de turno")


class OutcomeKind(StrEnum):
    COMPLETED = "completed"
    BOT_FAILURE = "bot_failure"
    INFRASTRUCTURE_FAILURE = "infrastructure_failure"
    NO_WINNER = "no_winner"


@dataclass(frozen=True, kw_only=True)
class AttemptOutcome(Message):
    match_id: str
    attempt_id: str
    generation: int
    worker_id: str
    kind: OutcomeKind
    winner_id: str
    reason: str

    def __post_init__(self) -> None:
        super().__post_init__()
        for value in (self.match_id, self.attempt_id, self.worker_id, self.reason):
            require_id(value)
        if self.generation <= 0:
            raise ValueError("Generación inválida")
        needs_winner = self.kind in (OutcomeKind.COMPLETED, OutcomeKind.BOT_FAILURE)
        if needs_winner != bool(self.winner_id.strip()):
            raise ValueError("Ganador incompatible con el tipo de resultado")

    def validate_for(self, attempt: AttemptSpec) -> None:
        if (self.match_id, self.attempt_id, self.generation) != (
            attempt.match.match_id,
            attempt.attempt_id,
            attempt.generation,
        ):
            raise ValueError("El resultado no corresponde al intento")
        if self.winner_id and self.winner_id not in attempt.match.competitor_ids:
            raise ValueError("El ganador no participa en la partida")


class WorkerStatus(StrEnum):
    AVAILABLE = "available"
    SUSPECTED = "suspected"
    LOST = "lost"


@dataclass(frozen=True, kw_only=True)
class WorkerInfo(Message):
    worker_id: str
    status: WorkerStatus
    capacity: int
    running_attempts: int

    def __post_init__(self) -> None:
        super().__post_init__()
        require_id(self.worker_id)
        if not 0 <= self.running_attempts <= self.capacity:
            raise ValueError("Capacidad inválida")


@dataclass(frozen=True, kw_only=True)
class DomainEvent(Message):
    event_id: str
    tournament_id: str
    match_id: str
    attempt_id: str
    event_type: str
    sequence: int
    occurred_at: str
    detail: str

    def __post_init__(self) -> None:
        from datetime import datetime

        super().__post_init__()
        for value in (self.event_id, self.tournament_id, self.event_type):
            require_id(value)
        if self.sequence <= 0:
            raise ValueError("Secuencia inválida")
        timestamp = datetime.fromisoformat(self.occurred_at)
        if timestamp.utcoffset() is None:
            raise ValueError("El evento requiere fecha con zona horaria")
