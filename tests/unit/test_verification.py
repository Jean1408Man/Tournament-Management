"""Historias de prueba de 5.2: una válida por formato y una rota por invariante."""

import json
import sys
from dataclasses import replace

import pytest

from tournament_management import cli
from tournament_management.tournaments.model import match_identifier, new_format, progress
from tournament_management.verification.evidence import TournamentEvidence, load
from tournament_management.verification.invariants import verify

TID = "t1"


def event(kind: str, match_id: str = "", attempt_id: str = "") -> dict:
    return {
        "event_id": f"{kind}-{match_id}-{attempt_id}",
        "tournament_id": TID,
        "match_id": match_id,
        "attempt_id": attempt_id,
        "event_type": kind,
        "sequence": 0,
        "occurred_at": "2026-10-29T00:00:00+00:00",
        "detail": kind,
    }


def numbered(events: list[dict]) -> list[dict]:
    return [{**e, "sequence": i} for i, e in enumerate(events, 1)]


def history(format_name: str, competitors: tuple[str, ...]) -> TournamentEvidence:
    """Torneo correcto: gana siempre el primero de cada pareja, a la primera."""
    replay = new_format(format_name, competitors)
    events, matches = [event("tournament.created")], []
    while replay.pending():
        for pairing in replay.pending():
            match_id = match_identifier(TID, pairing.match_id)
            attempt_id, winner = f"{pairing.match_id}-g1", pairing.competitors[0]
            events += [event("attempt.issued", match_id, attempt_id)]
            events += [event("result.accepted", match_id, attempt_id)]
            matches.append(
                {
                    "match_id": match_id,
                    "state": "completed",
                    "spec": {"competitor_ids": list(pairing.competitors)},
                    "winner_id": winner,
                    "current_attempt_id": attempt_id,
                    "accepted_attempt_id": attempt_id,
                    "attempts": [
                        {
                            "attempt_id": attempt_id,
                            "generation": 1,
                            "state": "accepted",
                            "outcome": {"kind": "completed", "winner_id": winner},
                        }
                    ],
                }
            )
            replay.record(pairing.match_id, winner)
    final = progress(replay)
    tournament = {
        "tournament_id": TID,
        "state": "completed",
        "format": format_name,
        "competitor_ids": list(competitors),
        "champion": final.get("champion"),
        "standings": [{"competitor_id": c, "wins": w} for c, w in final.get("standings", [])],
    }
    return TournamentEvidence(tournament, matches, len(matches), numbered(events))


def found(evidence: TournamentEvidence) -> set[tuple[str, str]]:
    return {(v["invariant"], v["match_id"]) for v in verify(evidence)}


@pytest.mark.parametrize(
    ("format_name", "size"), [("elimination", 2), ("elimination", 5), ("round_robin", 4)]
)
def test_valid_histories_pass(format_name: str, size: int) -> None:
    competitors = tuple(f"c{i}" for i in range(size))
    assert verify(history(format_name, competitors)) == []


def test_incomplete_evidence_is_not_a_pass() -> None:
    evidence = history("round_robin", ("a", "b", "c"))
    cut = replace(evidence, events=evidence.events[:-1])
    assert ("incomplete_evidence", cut.matches[-1]["match_id"]) in found(cut)
    assert ("incomplete_evidence", "") in found(replace(evidence, matches_total=4))
    evidence.tournament["state"] = "running"
    assert ("incomplete_evidence", "") in found(evidence)


def test_i1_double_acceptance() -> None:
    evidence = history("round_robin", ("a", "b", "c"))
    match = evidence.matches[0]
    evidence.events = numbered(
        [*evidence.events, event("result.accepted", match["match_id"], "m1-g1")]
    )
    assert ("I1", match["match_id"]) in found(evidence)


def test_i2_match_issued_before_its_dependencies() -> None:
    evidence = history("elimination", ("a", "b", "c", "d"))
    final = evidence.matches[-1]
    early = event("attempt.issued", final["match_id"], "early")
    evidence.events = numbered([evidence.events[0], early, *evidence.events[1:]])
    assert ("I2", final["match_id"]) in found(evidence)


def test_i2_independent_properties() -> None:
    evidence = history("round_robin", ("a", "b", "c"))
    evidence.matches.append({**evidence.matches[0], "match_id": "copy"})
    evidence.matches_total += 1
    assert any("la pareja" in v["detail"] for v in verify(evidence))

    evidence = history("elimination", ("a", "b", "c", "d"))
    evidence.matches[-1]["spec"]["competitor_ids"] = ["a", "b"]  # b ya perdió en r1
    assert any("b pierde 2 veces" in v["detail"] for v in verify(evidence))


def test_i3_stale_generation_accepted() -> None:
    evidence = history("elimination", ("a", "b"))
    match = evidence.matches[0]
    match_id = match["match_id"]
    match["attempts"].append(
        {"attempt_id": "r1-m1-g2", "generation": 2, "state": "running", "outcome": None}
    )
    created, issued, accepted = evidence.events
    evidence.events = numbered(
        [
            created,
            issued,
            event("attempt.lost", match_id, "r1-m1-g1"),
            event("attempt.issued", match_id, "r1-m1-g2"),
            accepted,
        ]
    )
    details = {v["detail"] for v in verify(evidence) if v["invariant"] == "I3"}
    assert "aceptada la generación 1 de 2" in details
    assert "aceptado tras darse por fallido, perdido o invalidado" in details


def test_i4_acceptance_after_cancellation() -> None:
    evidence = history("round_robin", ("a", "b", "c"))
    evidence.tournament["state"] = "cancelled"
    *before, last = evidence.events
    evidence.events = numbered([*before, event("tournament.cancelled"), last])
    assert ("I4", last["match_id"]) in found(evidence)


def test_i5_infrastructure_failure_accepted() -> None:
    evidence = history("elimination", ("a", "b"))
    match = evidence.matches[0]
    match["attempts"][0]["outcome"]["kind"] = "infrastructure_failure"
    assert ("I5", match["match_id"]) in found(evidence)


def test_tm_verify_reads_a_folder_and_fails_on_violations(tmp_path, monkeypatch, capsys) -> None:
    evidence = history("elimination", ("a", "b", "c"))

    def save() -> None:
        (tmp_path / f"{TID}.tournament.json").write_text(json.dumps(evidence.tournament))
        (tmp_path / f"{TID}.events.json").write_text(json.dumps(evidence.events))
        (tmp_path / f"{TID}.matches.json").write_text(
            json.dumps({"items": evidence.matches, "total": evidence.matches_total})
        )

    def run() -> tuple[int, dict]:
        monkeypatch.setattr(sys, "argv", ["tm", "verify", "--evidence", str(tmp_path)])
        with pytest.raises(SystemExit) as exit_info:
            cli.main()
        return exit_info.value.code, json.loads(capsys.readouterr().out)

    save()
    assert [e.tournament for e in load(tmp_path)] == [evidence.tournament]
    code, result = run()
    assert (code, result["ok"], result["not_checked"]) == (0, True, ["I6"])

    evidence.matches[0]["attempts"][0]["outcome"]["kind"] = "no_winner"
    save()
    code, result = run()
    assert (code, result["ok"]) == (1, False)
    assert result["tournaments"][0]["violations"][0]["invariant"] == "I5"


def seen(evidence: TournamentEvidence, events: list[dict] | None = None) -> list[dict]:
    """Registro del cliente: el 202 y cada evento tal como lo recibió."""
    received = evidence.events if events is None else events
    return [{"kind": "created", "tournament_id": TID}] + [
        {"kind": "event", "tournament_id": TID, "event": dict(e)} for e in received
    ]


def test_i6_client_view_matches_final_history() -> None:
    evidence = history("round_robin", ("a", "b", "c"))
    # Una repetición idéntica (entrega al menos una vez) no es violación.
    events = evidence.events
    evidence.client = seen(evidence, [*events[:3], events[2], *events[3:]])
    assert verify(evidence) == []


def test_i6_event_seen_by_client_and_lost() -> None:
    evidence = history("round_robin", ("a", "b", "c"))
    evidence.client = seen(evidence)
    lost = evidence.events.pop()
    details = {v["detail"] for v in verify(evidence) if v["invariant"] == "I6"}
    assert f"evento {lost['sequence']} visto por el cliente y ausente del historial" in details


def test_i6_changed_content_and_gap() -> None:
    evidence = history("round_robin", ("a", "b", "c"))
    received = [dict(e) for e in evidence.events]
    received[1]["detail"] = "otra cosa"
    del received[2]
    evidence.client = seen(evidence, received)
    details = {v["detail"] for v in verify(evidence) if v["invariant"] == "I6"}
    assert "evento 2 distinto del historial" in details
    assert "el cliente recibió 4 tras 2: hueco o desorden" in details


def test_i6_accepted_tournament_without_evidence(tmp_path) -> None:
    from tournament_management.verification.invariants import report

    evidence = history("elimination", ("a", "b"))
    client = {TID: seen(evidence), "ghost": [{"kind": "created", "tournament_id": "ghost"}]}
    result = report(tmp_path, [evidence], client)
    assert result["ok"] is False
    assert result["checked"][-1] == "I6" and result["not_checked"] == []
    assert result["violations"][0]["detail"] == "202 recibido para ghost sin evidencia del torneo"
