"""Límites aplicados a cada proceso de bot."""

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Limits:
    turn_timeout_ms: int = 1_000
    match_timeout_ms: int = 60_000
    memory_limit_bytes: int = 256 * 1024 * 1024
    output_limit_bytes: int = 64 * 1024

    def __post_init__(self) -> None:
        if min(
            self.turn_timeout_ms,
            self.match_timeout_ms,
            self.memory_limit_bytes,
            self.output_limit_bytes,
        ) <= 0:
            raise ValueError("Los límites deben ser positivos")
        if self.turn_timeout_ms > self.match_timeout_ms:
            raise ValueError("El límite de turno no puede superar el de partida")

    @property
    def stall_threshold_ms(self) -> int:
        """Salto entre dos vueltas del árbitro a partir del cual se le considera
        congelado (4.4, D4.8): la mitad del turno, nunca menos de 250 ms. Cada vuelta
        espera ≤ 50 ms, así que un bot lento nunca produce saltos de ese tamaño."""
        return max(250, self.turn_timeout_ms // 2)
