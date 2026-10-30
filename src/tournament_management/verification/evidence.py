"""Lectura de la carpeta que guarda dump() en experiments/scenarios/lib.sh.

Por torneo <t>, todo obtenido por HTTP (E2.2):
  <t>.tournament.json  GET /api/v1/tournaments/<t>
  <t>.matches.json     todas las páginas de GET .../matches: {"items": [...], "total": n}
  <t>.events.json      historial SSE leído hasta que el servidor cierra el flujo
Solo se leen archivos: otra persona puede repetir la verificación sobre ellos.
"""

import json
from dataclasses import dataclass
from pathlib import Path


@dataclass
class TournamentEvidence:
    tournament: dict
    matches: list[dict]
    matches_total: int
    events: list[dict]


def load(folder: Path) -> list[TournamentEvidence]:
    found = []
    for path in sorted(folder.glob("*.tournament.json")):
        name = path.name.removesuffix(".tournament.json")
        matches = _read(folder / f"{name}.matches.json")
        found.append(
            TournamentEvidence(
                tournament=_read(path),
                matches=matches["items"],
                matches_total=matches["total"],
                # El orden es la sequence (orden de commit), nunca occurred_at.
                events=sorted(_read(folder / f"{name}.events.json"), key=lambda e: e["sequence"]),
            )
        )
    return found


def _read(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))
