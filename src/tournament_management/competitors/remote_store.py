"""Lectura de bots por hash desde el coordinador, para los workers (etapa 4.2).

El worker no confía en la red: exige el tamaño anunciado y recalcula el
SHA-256. Cualquier fallo es ArtifactIntegrityError (un OSError), que el árbitro
trata como fallo de infraestructura y nunca como derrota del bot.
"""

import hashlib
import urllib.request

from tournament_management.competitors.artifact_store import ArtifactIntegrityError
from tournament_management.competitors.model import validate_artifact_hash


class RemoteArtifactStore:
    def __init__(self, base_url: str, token: str, *, max_bytes: int, timeout_ms: int) -> None:
        if not base_url.startswith(("http://", "https://")) or not token:
            raise ValueError("La descarga requiere URL http(s) del coordinador y token")
        self.base_url = base_url.rstrip("/")
        self.token = token
        self.max_bytes = max_bytes
        self.timeout = timeout_ms / 1000

    def get(self, artifact_hash: str) -> bytes:
        validate_artifact_hash(artifact_hash)
        request = urllib.request.Request(
            f"{self.base_url}/internal/artifacts/{artifact_hash}",
            headers={"Authorization": f"Bearer {self.token}"},
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                declared = int(response.headers.get("Content-Length", -1))
                content = response.read(self.max_bytes + 1)
        except Exception as exc:
            # Red caída, HTTP de error, plazo agotado o respuesta cortada
            # (http.client.IncompleteRead no es OSError): todo es infraestructura.
            raise ArtifactIntegrityError(
                f"Descarga fallida ({type(exc).__name__})"
            ) from exc
        if len(content) > self.max_bytes or len(content) != declared:
            raise ArtifactIntegrityError("Tamaño descargado inválido")
        if hashlib.sha256(content).hexdigest() != artifact_hash:
            raise ArtifactIntegrityError("El contenido descargado no corresponde al hash")
        return content
