"""Entrada de ejecución que fija los bots por hash y verifica antes de lanzarlos.

Los workers leen el volumen interno sin credenciales PostgreSQL. Cada intento
recibe copias privadas de los bytes verificados, no la ruta de una subida ni un
comando elegido por el cliente. Esto no constituye aislamiento de código hostil.
"""

import sys
import threading
from pathlib import Path
from tempfile import TemporaryDirectory

from tournament_management.competitors.artifact_store import ArtifactStore
from tournament_management.game.referee import run_match
from tournament_management.shared.messages import AttemptOutcome, AttemptSpec, OutcomeKind


def run_registered_match(
    attempt: AttemptSpec,
    store: ArtifactStore,
    *,
    worker_id: str = "local",
    cancel: threading.Event | None = None,
) -> AttemptOutcome:
    try:
        if sys.version_info[:2] != (3, 12):
            raise OSError("El runtime del worker debe ser Python 3.12")
        # Leer ambos antes de arrancar ninguno: un artefacto ausente/corrupto
        # representa un fallo de infraestructura, nunca una derrota de su bot.
        sources = tuple(store.get(value) for value in attempt.match.artifact_hashes)
        with TemporaryDirectory(prefix="tm-attempt-") as directory:
            commands = []
            for player, source in enumerate(sources):
                path = Path(directory) / f"player-{player}.py"
                path.write_bytes(source)
                path.chmod(0o400)
                commands.append((sys.executable, "-I", "-B", str(path)))
            return run_match(
                attempt,
                tuple(commands),
                worker_id=worker_id,
                cancel=cancel,
            )
    except OSError:
        return AttemptOutcome(
            match_id=attempt.match.match_id,
            attempt_id=attempt.attempt_id,
            generation=attempt.generation,
            worker_id=worker_id,
            kind=OutcomeKind.INFRASTRUCTURE_FAILURE,
            winner_id="",
            reason="artifact_or_runtime_unavailable",
        )
