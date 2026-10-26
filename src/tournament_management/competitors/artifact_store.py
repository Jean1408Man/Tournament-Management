"""Objetos inmutables por SHA-256, sin acceso HTTP ni dependencia de PostgreSQL.

Escribir primero un temporal y enlazarlo después evita que otro lector observe
una subida parcial. El enlace no reemplaza un objeto existente; dos escritores
con los mismos bytes convergen. fsync confirma contenido y entrada de directorio
antes de que la aplicación pueda anunciar disponibilidad en la base de datos.
"""

import hashlib
import os
import stat
import tempfile
from pathlib import Path
from typing import Protocol

from tournament_management.competitors.model import validate_artifact_hash


class ArtifactIntegrityError(OSError):
    """Objeto ausente, ilegible o cuyo contenido no coincide con su hash."""


class ArtifactStore(Protocol):
    """Archivo local hoy; descarga remota por hash en la etapa 4.2."""

    def put(self, content: bytes) -> str:
        """Publica bytes completos sin sobrescribir y retorna su SHA-256."""
        ...

    def get(self, artifact_hash: str) -> bytes:
        """Lee y verifica integridad en cada acceso, también en los workers."""
        ...


class FileArtifactStore:
    def __init__(self, directory: Path, *, max_bytes: int = 1_048_576) -> None:
        if type(max_bytes) is not int or max_bytes <= 0:
            raise ValueError("El límite de artefactos debe ser positivo")
        self.directory = Path(directory).resolve()
        self.max_bytes = max_bytes

    def _path(self, artifact_hash: str) -> Path:
        validate_artifact_hash(artifact_hash)
        return self.directory / f"{artifact_hash}.py"

    def put(self, content: bytes) -> str:
        if type(content) is not bytes or not 0 < len(content) <= self.max_bytes:
            raise ValueError("Archivo vacío o mayor que el límite de artefactos")
        artifact_hash = hashlib.sha256(content).hexdigest()
        target = self._path(artifact_hash)
        self._ensure_directory()
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(
                dir=self.directory,
                prefix=".upload-",
                delete=False,
            ) as stream:
                temporary = Path(stream.name)
                stream.write(content)
                stream.flush()
                os.fchmod(stream.fileno(), 0o444)
                os.fsync(stream.fileno())
            try:
                os.link(temporary, target)
            except FileExistsError:
                # No reparar ni sobrescribir silenciosamente un objeto corrupto.
                if self.get(artifact_hash) != content:
                    raise ArtifactIntegrityError("El objeto existente no coincide") from None
            # También sincronizar si otro escritor publicó primero; su enlace
            # podría no haber llegado a disco antes de nuestra transacción SQL.
            self._sync_directory()
            # Si otro escritor creó el directorio justo antes de entrar,
            # confirmar también su enlace antes de anunciar nuestro objeto.
            descriptor = os.open(self.directory.parent, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)
        return artifact_hash

    def get(self, artifact_hash: str) -> bytes:
        """Lectura acotada y verificada; funciona sobre un volumen de solo lectura."""
        path = self._path(artifact_hash)
        try:
            # NOFOLLOW evita enlaces simbólicos y NONBLOCK evita bloquearse en
            # una FIFO si alguien manipuló el directorio fuera de la aplicación.
            descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
            with os.fdopen(descriptor, "rb") as stream:
                info = os.fstat(stream.fileno())
                if not stat.S_ISREG(info.st_mode) or not 0 < info.st_size <= self.max_bytes:
                    raise ArtifactIntegrityError("Objeto no regular o tamaño inválido")
                content = stream.read(self.max_bytes + 1)
        except OSError as exc:
            raise ArtifactIntegrityError("No se pudo leer el artefacto íntegro") from exc
        if len(content) > self.max_bytes or hashlib.sha256(content).hexdigest() != artifact_hash:
            raise ArtifactIntegrityError("El contenido no corresponde al hash esperado")
        return content

    def _sync_directory(self) -> None:
        descriptor = os.open(self.directory, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)

    def _ensure_directory(self) -> None:
        missing = []
        directory = self.directory
        while not directory.exists():
            missing.append(directory)
            directory = directory.parent
        for directory in reversed(missing):
            directory.mkdir(mode=0o750, exist_ok=True)
            # Confirmar también la entrada del directorio recién creado en su
            # padre, antes de anunciar objetos que dependan de esa ruta.
            descriptor = os.open(directory.parent, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
