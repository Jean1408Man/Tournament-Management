"""Configuración validada: argumentos > entorno > valores predeterminados."""

from collections.abc import Mapping
from dataclasses import asdict, dataclass, fields
from pathlib import Path


@dataclass(frozen=True)
class Settings:
    role: str = "coordinator"
    backend: str = "local"
    ray_address: str = "auto"
    http_host: str = "127.0.0.1"
    http_port: int = 8000
    data_dir: str = "./data"
    log_level: str = "INFO"
    turn_timeout_ms: int = 1000
    match_timeout_ms: int = 60000
    attempt_timeout_ms: int = 90000
    memory_limit_bytes: int = 268435456
    output_limit_bytes: int = 1048576
    max_retries: int = 3
    retry_delay_ms: int = 1000

    def __post_init__(self) -> None:
        if self.role not in {"coordinator", "ray-head", "ray-worker"}:
            raise ValueError("TM_ROLE debe ser coordinator, ray-head o ray-worker")
        if self.backend not in {"local", "ray"}:
            raise ValueError("TM_BACKEND debe ser local o ray")
        if self.log_level not in {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}:
            raise ValueError("TM_LOG_LEVEL inválido")
        for name in ("ray_address", "http_host", "data_dir"):
            if not getattr(self, name).strip():
                raise ValueError(f"TM_{name.upper()} no puede estar vacío")
        if not 1 <= self.http_port <= 65535:
            raise ValueError("TM_HTTP_PORT debe estar entre 1 y 65535")
        for name in (
            "turn_timeout_ms",
            "match_timeout_ms",
            "attempt_timeout_ms",
            "memory_limit_bytes",
            "output_limit_bytes",
        ):
            if getattr(self, name) <= 0:
                raise ValueError(f"TM_{name.upper()} debe ser positivo")
        if self.max_retries < 0 or self.retry_delay_ms < 0:
            raise ValueError("Reintentos y espera no pueden ser negativos")
        if not self.turn_timeout_ms <= self.match_timeout_ms < self.attempt_timeout_ms:
            raise ValueError("Se requiere turno <= partida < plazo del intento")

    @classmethod
    def load(cls, arguments: Mapping[str, object], environ: Mapping[str, str]) -> "Settings":
        defaults = asdict(cls())
        resolved = {}
        for field in fields(cls):
            name = field.name
            value = arguments.get(name)
            if value is None:
                value = environ.get(f"TM_{name.upper()}", defaults[name])
            if isinstance(defaults[name], int):
                try:
                    value = int(str(value))
                except ValueError as exc:
                    raise ValueError(f"TM_{name.upper()} debe ser un entero") from exc
            resolved[name] = value
        return cls(**resolved)

    @property
    def data_path(self) -> Path:
        return Path(self.data_dir).expanduser().resolve()
