"""Registro durable: validar → publicar bytes → confirmar metadatos disponibles.

No hay transacción distribuida entre archivos y PostgreSQL. Este orden admite
objetos huérfanos si falla SQL, pero impide anunciar una subida parcial. Nunca
se elimina un objeto al fallar SQL: otra inscripción podría estar usándolo.
"""

import hashlib
import json
from collections.abc import Callable
from pathlib import PurePath
from typing import BinaryIO
from uuid import uuid4

from tournament_management.competitors.artifact_store import ArtifactIntegrityError, ArtifactStore
from tournament_management.competitors.model import ArtifactSpec, RegisteredCompetitor
from tournament_management.shared.errors import ConflictError
from tournament_management.shared.messages import require_id
from tournament_management.shared.unit_of_work import UnitOfWork


class CompetitorService:
    def __init__(
        self,
        work_factory: Callable[[], UnitOfWork],
        store: ArtifactStore,
        *,
        max_bytes: int = 1_048_576,
    ) -> None:
        if type(max_bytes) is not int or max_bytes <= 0:
            raise ValueError("El límite de subida debe ser positivo")
        self.work_factory = work_factory
        self.store = store
        self.max_bytes = max_bytes

    def register(
        self,
        source: BinaryIO,
        *,
        filename: str,
        name: str,
        idempotency_key: str,
        competitor_id: str | None = None,
        protocol: str = "nim-jsonl",
        protocol_version: int = 1,
        runtime: str = "python3.12",
    ) -> RegisteredCompetitor:
        """Acepta Python UTF-8 válido sintácticamente; no importa ni ejecuta el bot."""
        if type(idempotency_key) is not str:
            raise ValueError("La clave de idempotencia debe ser texto")
        require_id(idempotency_key)
        if not isinstance(filename, str) or PurePath(filename).suffix != ".py":
            raise ValueError("Se requiere un archivo con extensión .py")
        content = self._read(source)
        try:
            code = content.decode("utf-8")
            compile(code, "<registered-bot>", "exec", dont_inherit=True)
        except (UnicodeError, SyntaxError, ValueError, RecursionError) as exc:
            raise ValueError("El bot debe ser Python UTF-8 sintácticamente válido") from exc
        artifact = ArtifactSpec(
            artifact_hash=hashlib.sha256(content).hexdigest(),
            size_bytes=len(content),
            protocol=protocol,
            protocol_version=protocol_version,
            runtime=runtime,
        )
        registered = RegisteredCompetitor(
            competitor_id=competitor_id if competitor_id is not None else str(uuid4()),
            name=name,
            artifact=artifact,
        )
        # El ID generado se excluye de la huella para que repetir una petición
        # sin ID explícito recupere el recurso original, incluso tras reiniciar.
        request = {"name": name, "competitor_id": competitor_id, "artifact": artifact.to_json()}
        digest = hashlib.sha256(json.dumps(request, sort_keys=True).encode()).hexdigest()
        published_hash = self.store.put(content)
        if published_hash != artifact.artifact_hash:
            raise ArtifactIntegrityError("El almacén publicó un hash diferente")
        with self.work_factory() as work:
            identifier = work.idempotency.claim(
                "competitor.register",
                idempotency_key,
                digest,
                registered.competitor_id,
            )
            work.registry.add_artifact(
                artifact.artifact_hash,
                artifact.size_bytes,
                protocol=artifact.protocol,
                protocol_version=artifact.protocol_version,
                runtime=artifact.runtime,
            )
            work.registry.mark_available(artifact.artifact_hash)
            existing = work.registry.find_competitor(identifier)
            if existing is not None:
                if (existing.name, existing.artifact) != (registered.name, artifact):
                    raise ConflictError("El identificador ya pertenece a otro bot")
                registered = existing
            else:
                work.registry.add_competitor(identifier, name, artifact.artifact_hash)
                registered = RegisteredCompetitor(
                    competitor_id=identifier,
                    name=name,
                    artifact=artifact,
                )
            work.commit()
        return registered

    def get(self, competitor_id: str) -> RegisteredCompetitor:
        with self.work_factory() as work:
            registered = work.registry.get_competitor(competitor_id)
        content = self.store.get(registered.artifact.artifact_hash)
        if len(content) != registered.artifact.size_bytes:
            raise ArtifactIntegrityError("El tamaño no coincide con los metadatos registrados")
        return registered

    def content(self, competitor_id: str) -> bytes:
        with self.work_factory() as work:
            registered = work.registry.get_competitor(competitor_id)
        content = self.store.get(registered.artifact.artifact_hash)
        if len(content) != registered.artifact.size_bytes:
            raise ArtifactIntegrityError("El tamaño no coincide con los metadatos registrados")
        return content

    def _read(self, source: BinaryIO) -> bytes:
        chunks = []
        total = 0
        while total <= self.max_bytes:
            chunk = source.read(min(65536, self.max_bytes + 1 - total))
            if not isinstance(chunk, bytes):
                raise ValueError("La subida debe ser binaria")
            if not chunk:
                break
            chunks.append(chunk)
            total += len(chunk)
        if total == 0 or total > self.max_bytes:
            raise ValueError(f"El archivo debe contener entre 1 y {self.max_bytes} bytes")
        return b"".join(chunks)
