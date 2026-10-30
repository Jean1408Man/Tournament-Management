"""Invariantes I1–I5 sobre la evidencia de un torneo (5.2).

Detecta violaciones en las ejecuciones observadas; no demuestra que no existan.
El orden es la sequence de cada torneo: se asigna con el torneo bloqueado hasta
el commit, así que es el orden en que la base aceptó los hechos.

Tipos de evento en los que se apoya (no son contrato público): attempt.issued,
attempt.failed, attempt.lost, attempt.invalidated, result.accepted,
tournament.blocked y tournament.cancelled.

I2 repite el torneo con los formatos del servidor; un error compartido en ellos
pasaría inadvertido, por eso se añaden propiedades independientes (E2.8 c).
I6 (lo que vio el cliente) llega con el generador de carga de 5.1 (E2.7 c).
"""

from collections import Counter
from pathlib import Path

from tournament_management.tournaments.model import match_identifier, new_format, progress
from tournament_management.verification.evidence import TournamentEvidence

CHECKED = ("incomplete_evidence", "I1", "I2", "I3", "I4", "I5")
NOT_CHECKED = ("I6",)
TERMINAL = {"completed", "blocked", "cancelled"}
ACCEPTABLE = {"completed", "bot_failure"}
CLOSED = {"attempt.failed", "attempt.lost", "attempt.invalidated"}


def report(folder: Path, evidence: list[TournamentEvidence]) -> dict:
    tournaments = [
        {
            "tournament_id": item.tournament["tournament_id"],
            "state": item.tournament["state"],
            "violations": verify(item),
        }
        for item in evidence
    ]
    return {
        "evidence": str(folder),
        "ok": not any(t["violations"] for t in tournaments),
        "checked": list(CHECKED),
        "not_checked": list(NOT_CHECKED),
        "tournaments": tournaments,
    }


def verify(evidence: TournamentEvidence) -> list[dict]:
    return [
        *completeness(evidence),
        *uniqueness(evidence),
        *dependencies(evidence),
        *current_attempt(evidence),
        *cancellation(evidence),
        *no_infrastructure_defeats(evidence),
    ]


def violation(invariant: str, detail: str, match_id: str = "", attempt_id: str = "") -> dict:
    return {
        "invariant": invariant,
        "match_id": match_id,
        "attempt_id": attempt_id,
        "detail": detail,
    }


def _accepted_events(evidence: TournamentEvidence) -> list[dict]:
    return [e for e in evidence.events if e["event_type"] == "result.accepted"]


def completeness(evidence: TournamentEvidence) -> list[dict]:
    """Sin evidencia completa no se da nada por bueno."""
    found = []
    state = evidence.tournament["state"]
    if state not in TERMINAL:
        found.append(violation("incomplete_evidence", f"torneo en estado {state}, no terminal"))
    if len(evidence.matches) != evidence.matches_total:
        found.append(
            violation(
                "incomplete_evidence",
                f"{len(evidence.matches)} partidas guardadas de {evidence.matches_total}",
            )
        )
    sequences = [e["sequence"] for e in evidence.events]
    if sequences != list(range(1, len(sequences) + 1)):
        found.append(violation("incomplete_evidence", "secuencias con huecos o repetidas"))
    seen = {e["match_id"] for e in _accepted_events(evidence)}
    for match in evidence.matches:
        if match["accepted_attempt_id"] and match["match_id"] not in seen:
            found.append(
                violation(
                    "incomplete_evidence",
                    "partida aceptada sin result.accepted en el historial",
                    match["match_id"],
                    match["accepted_attempt_id"],
                )
            )
    if state in {"blocked", "cancelled"} and not any(
        e["event_type"] == f"tournament.{state}" for e in evidence.events
    ):
        found.append(violation("incomplete_evidence", f"falta el evento tournament.{state}"))
    return found


def uniqueness(evidence: TournamentEvidence) -> list[dict]:
    """I1: como mucho un resultado aceptado por partida."""
    found = [
        violation("I1", f"{count} result.accepted para la misma partida", match_id)
        for match_id, count in Counter(e["match_id"] for e in _accepted_events(evidence)).items()
        if count > 1
    ]
    for match in evidence.matches:
        accepted = [a for a in match["attempts"] if a["state"] == "accepted"]
        if len(accepted) > 1:
            found.append(violation("I1", f"{len(accepted)} intentos aceptados", match["match_id"]))
    return found


def dependencies(evidence: TournamentEvidence) -> list[dict]:
    """I2: nada se lanza ni se acepta antes de estar lista; el cuadro final cuadra."""
    tournament = evidence.tournament
    tournament_id = tournament["tournament_id"]
    competitors = tuple(tournament["competitor_ids"])
    replay = new_format(tournament["format"], competitors)
    winners = {m["match_id"]: m["winner_id"] for m in evidence.matches}
    found = []
    for event in evidence.events:
        kind, match_id, attempt_id = event["event_type"], event["match_id"], event["attempt_id"]
        if kind not in {"attempt.issued", "result.accepted"}:
            continue
        ready = {match_identifier(tournament_id, p.match_id): p.match_id for p in replay.pending()}
        if match_id not in ready:
            detail = f"{kind} para una partida que no estaba lista"
            found.append(violation("I2", detail, match_id, attempt_id))
        elif kind == "result.accepted":
            try:
                replay.record(ready[match_id], winners.get(match_id))
            except ValueError as exc:
                detail = f"resultado incompatible: {exc}"
                found.append(violation("I2", detail, match_id, attempt_id))

    state = tournament["state"]
    if (state == "completed") != (replay.status == "completed"):
        found.append(violation("I2", f"torneo {state}; repetir su historial da {replay.status}"))
    elif state == "completed":
        expected = progress(replay)
        if "champion" in expected:
            observed, wanted = tournament["champion"], expected["champion"]
        else:
            observed = [(r["competitor_id"], r["wins"]) for r in tournament["standings"]]
            wanted = list(expected["standings"])
        if observed != wanted:
            found.append(violation("I2", f"clasificación final {observed}, esperada {wanted}"))
    return found + _independent_properties(evidence, competitors)


def _independent_properties(evidence: TournamentEvidence, competitors: tuple) -> list[dict]:
    """Comprobaciones que no usan las clases de formato (E2.8 c)."""
    found = []
    completed = evidence.tournament["state"] == "completed"
    decided = [m for m in evidence.matches if m["state"] == "completed"]
    n = len(competitors)
    expected = n * (n - 1) // 2
    if evidence.tournament["format"] == "round_robin":
        pairs = Counter(frozenset(m["spec"]["competitor_ids"]) for m in evidence.matches)
        for pair, count in pairs.items():
            if count > 1:
                found.append(violation("I2", f"la pareja {sorted(pair)} tiene {count} partidas"))
    else:
        expected = n - 1
        losers = Counter(
            next(c for c in m["spec"]["competitor_ids"] if c != m["winner_id"]) for m in decided
        )
        for competitor, count in losers.items():
            if count > 1:
                found.append(violation("I2", f"{competitor} pierde {count} veces"))
        champion = evidence.tournament["champion"]
        unbeaten = sorted(set(competitors) - set(losers))
        if completed and unbeaten != [champion]:
            found.append(violation("I2", f"invictos {unbeaten}; campeón {champion}"))
    if completed and len(decided) != expected:
        found.append(violation("I2", f"{len(decided)} partidas decididas; esperadas {expected}"))
    return found


def current_attempt(evidence: TournamentEvidence) -> list[dict]:
    """I3: lo aceptado es el intento vigente (última generación, aún abierto)."""
    matches = {m["match_id"]: m for m in evidence.matches}
    issued, closed, found = set(), set(), []
    for event in evidence.events:
        kind, match_id, attempt_id = event["event_type"], event["match_id"], event["attempt_id"]
        if kind == "attempt.issued":
            issued.add(attempt_id)
        elif kind in CLOSED:
            closed.add(attempt_id)
        elif kind == "result.accepted":
            match = matches.get(match_id, {"attempts": []})
            attempts = {a["attempt_id"]: a for a in match["attempts"]}
            attempt = attempts.get(attempt_id)

            problems = []
            if attempt_id not in issued:
                problems.append("aceptado sin attempt.issued previo")
            if attempt_id in closed:
                problems.append("aceptado tras darse por fallido, perdido o invalidado")
            if attempt is None:
                problems.append("intento aceptado ausente de la consulta final")
            else:
                latest = max(a["generation"] for a in attempts.values())
                if attempt["generation"] != latest:
                    problems.append(f"aceptada la generación {attempt['generation']} de {latest}")
                if match["accepted_attempt_id"] != attempt_id or attempt["state"] != "accepted":
                    problems.append("el historial y la consulta final discrepan")
            found += [violation("I3", p, match_id, attempt_id) for p in problems]
    return found


def cancellation(evidence: TournamentEvidence) -> list[dict]:
    """I4: tras tournament.cancelled no se emite ni se acepta nada."""
    cancelled, found = False, []
    for event in evidence.events:
        kind = event["event_type"]
        if kind == "tournament.cancelled":
            cancelled = True
        elif cancelled and kind in {"attempt.issued", "result.accepted"}:
            found.append(
                violation("I4", f"{kind} tras cancelar", event["match_id"], event["attempt_id"])
            )
    return found


def no_infrastructure_defeats(evidence: TournamentEvidence) -> list[dict]:
    """I5: un fallo de infraestructura nunca decide una partida."""
    found = []
    for match in evidence.matches:
        accepted = match["accepted_attempt_id"]
        if not accepted:
            continue
        attempt = next((a for a in match["attempts"] if a["attempt_id"] == accepted), {})
        kind = (attempt.get("outcome") or {}).get("kind")
        if kind not in ACCEPTABLE:
            detail = f"resultado aceptado de tipo {kind}"
            found.append(violation("I5", detail, match["match_id"], accepted))
    return found
