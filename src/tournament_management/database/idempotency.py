"""Claves de idempotencia compartidas por el registro de bots y la creación de torneos."""

from typing import Any

from tournament_management.shared.errors import ConflictError
from tournament_management.shared.messages import require_id


class _PostgresIdempotency:
    def __init__(self, connection: Any) -> None:
        self.connection = connection

    def claim(self, scope: str, key: str, request_hash: str, resource_id: str) -> str:
        """La unicidad espera al creador concurrente y devuelve su recurso confirmado.

        La reserva y la creación viven en la misma transacción: nunca se conserva
        una clave que anuncie un recurso cuya creación se revirtió.
        """
        require_id(key)
        with self.connection.cursor() as cursor:
            cursor.execute(
                """INSERT INTO idempotency_keys (scope, key, request_hash, resource_id)
                   VALUES (%s, %s, %s, %s) ON CONFLICT DO NOTHING""",
                (scope, key, request_hash, resource_id),
            )
            cursor.execute(
                "SELECT * FROM idempotency_keys WHERE scope = %s AND key = %s",
                (scope, key),
            )
            row = cursor.fetchone()
            if row["request_hash"] != request_hash:
                raise ConflictError("Clave de idempotencia utilizada con otro contenido")
            return row["resource_id"]
