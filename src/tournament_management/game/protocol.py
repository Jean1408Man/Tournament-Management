"""Codificación y validación básica del protocolo JSONL de bots."""

from __future__ import annotations

import json
from typing import TextIO

MAX_MESSAGE_BYTES = 64 * 1024
PROTOCOL = "nim-jsonl"
VERSION = 1


def encode(message: dict[str, object]) -> str:
    """Serializa un mensaje y garantiza el límite de transporte."""
    if type(message) is not dict:
        raise ValueError("El mensaje debe ser un objeto")
    raw = json.dumps(message, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
    line = raw + "\n"
    if len(line.encode("utf-8")) > MAX_MESSAGE_BYTES:
        raise ValueError("Mensaje demasiado grande")
    return line


def write_message(stream: TextIO, message: dict[str, object]) -> None:
    stream.write(encode(message))
    stream.flush()


def read_message(stream: TextIO) -> dict[str, object] | None:
    line = stream.readline(MAX_MESSAGE_BYTES + 1)
    if not line:
        return None
    if not line.endswith("\n"):
        raise ValueError("Mensaje sin fin de línea")
    if len(line.encode("utf-8")) > MAX_MESSAGE_BYTES:
        raise ValueError("Mensaje demasiado grande")
    message = json.loads(line)
    if type(message) is not dict:
        raise ValueError("El mensaje debe ser un objeto")
    return message


def require_fields(message: dict[str, object], message_type: str, fields: set[str]) -> None:
    if message.get("type") != message_type or set(message) != fields:
        raise ValueError(f"Mensaje {message_type} inválido")
