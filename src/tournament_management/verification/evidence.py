"""Lectura de la carpeta que guarda dump() en experiments/scenarios/lib.sh.

Por torneo <t>, todo obtenido por HTTP (E2.2):
  <t>.tournament.json  GET /api/v1/tournaments/<t>
  <t>.matches.json     todas las páginas de GET .../matches: {"items": [...], "total": n}
  <t>.events.json      historial SSE leído hasta que el servidor cierra el flujo
  client.jsonl         opcional (5.1): lo que el cliente recibió en vivo, una
                       línea por 202 (created) o evento SSE (event), con su hora
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
    # Registro del cliente de este torneo; None si la carpeta no tiene client.jsonl.
    client: list[dict] | None = None


def load(folder: Path) -> list[TournamentEvidence]:
    client = load_client(folder)
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
                client=None if client is None else client.get(name, []),
            )
        )
    return found


def load_client(folder: Path) -> dict[str, list[dict]] | None:
    """client.jsonl agrupado por torneo, en el orden en que se recibió."""
    path = folder / "client.jsonl"
    if not path.exists():
        return None
    records: dict[str, list[dict]] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            record = json.loads(line)
            records.setdefault(record["tournament_id"], []).append(record)
    return records


def _read(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))
