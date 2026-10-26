"""Registro inmutable de identidad y contrato de ejecución de un bot.

El nombre del archivo subido no se usa como ruta de almacenamiento. La identidad
física es el SHA-256 de sus bytes y un competidor fija ese artefacto para siempre.
"""

from dataclasses import dataclass

from tournament_management.shared.messages import Message, require_id


def validate_artifact_hash(value: str) -> None:
    if (
        type(value) is not str
        or len(value) != 64
        or any(c not in "0123456789abcdef" for c in value)
    ):
        raise ValueError("Se requiere un hash SHA-256 hexadecimal en minúsculas")


@dataclass(frozen=True, kw_only=True)
class ArtifactSpec(Message):
    artifact_hash: str
    size_bytes: int
    protocol: str = "nim-jsonl"
    protocol_version: int = 1
    runtime: str = "python3.12"

    def __post_init__(self) -> None:
        super().__post_init__()
        validate_artifact_hash(self.artifact_hash)
        if self.size_bytes <= 0:
            raise ValueError("El archivo Python no puede estar vacío")
        if self.protocol != "nim-jsonl" or self.protocol_version != 1:
            raise ValueError("Solo se admite nim-jsonl versión 1")
        if self.runtime != "python3.12":
            raise ValueError("Solo se admite el runtime python3.12")


@dataclass(frozen=True, kw_only=True)
class RegisteredCompetitor(Message):
    competitor_id: str
    name: str
    artifact: ArtifactSpec

    def __post_init__(self) -> None:
        super().__post_init__()
        require_id(self.competitor_id)
        require_id(self.name)
        if len(self.name) > 200:
            raise ValueError("El nombre no puede superar 200 caracteres")
