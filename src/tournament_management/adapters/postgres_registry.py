"""Registro durable y disponibilidad: solo anunciar tras publicar los bytes."""

from typing import Any

from tournament_management.application.errors import ConflictError
from tournament_management.domain.competitors import ArtifactSpec, RegisteredCompetitor
from tournament_management.domain.messages import require_id


class _PostgresRegistry:
    def __init__(self, connection: Any) -> None:
        self.connection = connection

    def add_artifact(
        self,
        artifact_hash: str,
        size_bytes: int,
        *,
        protocol: str = "nim-jsonl",
        protocol_version: int = 1,
        runtime: str = "python3.12",
    ) -> None:
        if (
            len(artifact_hash) != 64
            or any(c not in "0123456789abcdef" for c in artifact_hash)
            or type(size_bytes) is not int
            or size_bytes < 0
        ):
            raise ValueError("Hash o tamaño de artefacto inválido")
        if protocol != "nim-jsonl" or type(protocol_version) is not int or protocol_version != 1:
            raise ValueError("Protocolo no soportado")
        if runtime != "python3.12":
            raise ValueError("Runtime no permitido")
        with self.connection.cursor() as cursor:
            cursor.execute(
                """INSERT INTO artifacts
                   (artifact_hash, size_bytes, protocol, protocol_version, runtime)
                   VALUES (%s, %s, %s, %s, %s) ON CONFLICT DO NOTHING""",
                (artifact_hash, size_bytes, protocol, protocol_version, runtime),
            )
            cursor.execute("SELECT * FROM artifacts WHERE artifact_hash = %s", (artifact_hash,))
            row = cursor.fetchone()
            if (row["size_bytes"], row["protocol"], row["protocol_version"], row["runtime"]) != (
                size_bytes,
                protocol,
                protocol_version,
                runtime,
            ):
                raise ValueError("El hash ya tiene otros metadatos")

    def add_competitor(self, competitor_id: str, name: str, artifact_hash: str) -> None:
        require_id(competitor_id)
        require_id(name)
        with self.connection.cursor() as cursor:
            cursor.execute(
                """INSERT INTO competitors (competitor_id, name, artifact_hash)
                   VALUES (%s, %s, %s) ON CONFLICT (competitor_id) DO NOTHING""",
                (competitor_id, name, artifact_hash),
            )
            cursor.execute(
                "SELECT name, artifact_hash FROM competitors WHERE competitor_id = %s",
                (competitor_id,),
            )
            row = cursor.fetchone()
            if (row["name"], row["artifact_hash"]) != (name, artifact_hash):
                raise ConflictError("El identificador ya pertenece a otro bot")

    def mark_available(self, artifact_hash: str) -> None:
        """Solo invocar después de que ArtifactStore.put haya confirmado el objeto."""
        with self.connection.cursor() as cursor:
            cursor.execute(
                """UPDATE artifacts SET available = TRUE,
                   published_at = COALESCE(published_at, CURRENT_TIMESTAMP)
                   WHERE artifact_hash = %s""",
                (artifact_hash,),
            )
            if cursor.rowcount != 1:
                raise KeyError(artifact_hash)

    def find_competitor(self, competitor_id: str) -> RegisteredCompetitor | None:
        return self._read_competitor(competitor_id, require_available=False)

    def get_competitor(self, competitor_id: str) -> RegisteredCompetitor:
        registered = self._read_competitor(competitor_id, require_available=True)
        if registered is None:
            raise KeyError(competitor_id)
        return registered

    def _read_competitor(
        self,
        competitor_id: str,
        *,
        require_available: bool,
    ) -> RegisteredCompetitor | None:
        with self.connection.cursor() as cursor:
            cursor.execute(
                """SELECT c.competitor_id, c.name, a.artifact_hash, a.size_bytes,
                          a.protocol, a.protocol_version, a.runtime
                   FROM competitors c JOIN artifacts a USING (artifact_hash)
                   WHERE c.competitor_id = %s AND (a.available OR NOT %s)""",
                (competitor_id, require_available),
            )
            row = cursor.fetchone()
        if row is None:
            return None
        return RegisteredCompetitor(
            competitor_id=row["competitor_id"],
            name=row["name"],
            artifact=ArtifactSpec(
                **{
                    key: row[key]
                    for key in (
                        "artifact_hash",
                        "size_bytes",
                        "protocol",
                        "protocol_version",
                        "runtime",
                    )
                }
            ),
        )


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
