"""Lectura de la evidencia guardada por dump() y comprobaciones comunes (4.4).

Lo importan los fragmentos de Python de cada escenario:
    sys.path.insert(0, SCENARIO_DIR); from checks import Evidence
"""

import json
import sys
from pathlib import Path


class Evidence:
    def __init__(self, results: str, tournament: str) -> None:
        base = Path(results)
        self.tournament = json.loads((base / f"{tournament}.tournament.json").read_text())
        self.matches = json.loads((base / f"{tournament}.matches.json").read_text())["items"]
        self.events = json.loads((base / f"{tournament}.events.json").read_text())
        self.failures = 0

    def check(self, description: str, ok: bool) -> None:
        print(("OK    " if ok else "FALLO ") + description)
        self.failures += not ok

    def attempts(self) -> list[dict]:
        return [attempt for match in self.matches for attempt in match["attempts"]]

    def of_type(self, kind: str) -> list[dict]:
        return [event for event in self.events if event["event_type"] == kind]

    def dispatched_worker(self, attempt_id: str) -> str:
        for event in self.of_type("attempt.dispatched"):
            if event["attempt_id"] == attempt_id:
                return event["detail"].removeprefix("worker=")
        return ""

    def one_result_per_completed_match(self) -> None:
        completed = [m for m in self.matches if m["state"] == "completed"]
        accepted = self.of_type("result.accepted")
        self.check(
            f"un único result.accepted por partida completada "
            f"({len(accepted)} eventos, {len(completed)} partidas)",
            len(accepted) == len(completed)
            and len({e["match_id"] for e in accepted}) == len(accepted),
        )
        self.check(
            "ninguna partida tiene más de un intento aceptado",
            all(sum(a["state"] == "accepted" for a in m["attempts"]) <= 1 for m in self.matches),
        )

    def done(self) -> None:
        sys.exit(self.failures)
