"""Servidor de prueba que permite SIGKILL en límites observables del commit.

Se ejecuta como proceso independiente. El único comportamiento instrumentado es
la pausa antes/después de accept; HTTP, bots, archivos y PostgreSQL son reales.
"""

import json
import socket
import sys
import time
from pathlib import Path

import uvicorn

from tournament_management.app import create_app
from tournament_management.application.persistent_tournament_service import (
    PersistentTournamentService,
)
from tournament_management.config import Settings


def main():
    configuration, marker, phase, descriptor = sys.argv[1:]
    original = PersistentTournamentService.accept

    def accept(self, outcome):
        if phase == "after":
            original(self, outcome)
        temporary = Path(marker).with_suffix(".tmp")
        temporary.write_text(outcome.to_json())
        temporary.replace(marker)
        # El test mata el proceso: no se intenta simular un cierre limpio.
        while True:
            time.sleep(0.05)

    PersistentTournamentService.accept = accept
    app = create_app(Settings(**json.loads(Path(configuration).read_text())))
    server = uvicorn.Server(uvicorn.Config(app, ws="none", log_level="error"))
    with socket.socket(fileno=int(descriptor)) as listener:
        server.run(sockets=[listener])


if __name__ == "__main__":
    main()
