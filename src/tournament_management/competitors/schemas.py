"""Metadatos públicos; el código se sube como cuerpo binario acotado."""

from typing import Literal

from pydantic import BaseModel


class ArtifactResponse(BaseModel):
    schema_version: Literal[1] = 1
    artifact_hash: str
    size_bytes: int
    protocol: Literal["nim-jsonl"]
    protocol_version: Literal[1]
    runtime: Literal["python3.12"]


class CompetitorResponse(BaseModel):
    schema_version: Literal[1] = 1
    competitor_id: str
    name: str
    artifact: ArtifactResponse
