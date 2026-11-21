"""Política de ejecución fijada por el servidor, nunca por el cliente HTTP."""

from dataclasses import dataclass
from uuid import uuid4

from tournament_management.shared.messages import AttemptSpec, MatchSpec


@dataclass(frozen=True)
class ExecutionPolicy:
    turn_timeout_ms: int
    match_timeout_ms: int
    attempt_timeout_ms: int
    memory_limit_bytes: int
    output_limit_bytes: int
    max_retries: int
    retry_delay_ms: int
    capacity_wait_ms: int = 300_000
    max_running_attempts: int = 64
    # Espera de laboratorio del worker antes de cada partida (TM_WORKER_DELAY_MS).
    worker_delay_ms: int = 0

    @property
    def attempt_deadline_ms(self) -> int:
        """Plazo real del intento: el configurado más la espera del worker."""
        return self.attempt_timeout_ms + self.worker_delay_ms

    def attempt(self, match: MatchSpec, generation: int) -> AttemptSpec:
        return AttemptSpec(
            attempt_id=str(uuid4()),
            generation=generation,
            match=match,
            turn_timeout_ms=self.turn_timeout_ms,
            match_timeout_ms=self.match_timeout_ms,
            memory_limit_bytes=self.memory_limit_bytes,
            output_limit_bytes=self.output_limit_bytes,
        )
