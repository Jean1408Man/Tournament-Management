"""Identidad y capacidad propias, sin exponer referencias de Ray."""

from typing import Literal

from pydantic import BaseModel


class WorkerResponse(BaseModel):
    schema_version: Literal[1]
    worker_id: str
    status: Literal["available", "suspected", "lost"]
    capacity: int
    running_attempts: int
