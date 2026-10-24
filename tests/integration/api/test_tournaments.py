"""Registrar, crear, ejecutar, consultar y repetir solicitudes por HTTP real."""

from concurrent.futures import ThreadPoolExecutor

import pytest


@pytest.mark.parametrize("format_name,count,played", [("elimination", 5, 4), ("round_robin", 4, 6)])
def test_end_to_end_both_formats(api, format_name, count, played):
    identifiers = tuple(f"bot-{index}" for index in range(count))
    for identifier in identifiers:
        status, headers, registered = api.register(identifier)
        assert status == 201
        assert len(registered["artifact"]["artifact_hash"]) == 64
        assert api.request("GET", headers["Location"])[2] == registered
    status, headers, accepted = api.create(format_name=format_name, identifiers=identifiers)
    assert status == 202
    assert headers["Location"] == accepted["status_url"]
    tournament = api.terminal(accepted["tournament_id"])
    assert tournament["state"] == "completed"
    assert tournament["statistics"]["completed"] == played
    assert tournament["statistics"]["attempts_total"] == played
    assert tournament["competitor_ids"] == list(identifiers)
    if format_name == "elimination":
        assert tournament["champion"] in identifiers
        assert len(tournament["bracket"]) == played
        assert all(entry["state"] == "completed" for entry in tournament["bracket"])
    else:
        assert sum(row["wins"] for row in tournament["standings"]) == played
        assert tournament["bracket"] == []
    status, _, matches = api.request("GET", accepted["status_url"] + "/matches?limit=2")
    assert status == 200
    assert matches["total"] == played
    assert len(matches["items"]) == 2
    for match in matches["items"]:
        assert match["state"] == "completed"
        assert match["attempts"][0]["outcome"]["winner_id"] == match["winner_id"]
    assert (
        api.request("GET", accepted["status_url"] + f"/matches?offset={played}")[2]["items"] == []
    )


def test_lost_response_repeated_creation_and_conflict(api):
    api.register("a")
    api.register("b")
    first = api.create()[2]
    assert api.create()[2] == first
    assert api.create(seed=99)[0] == 409
    api.terminal(first["tournament_id"])
    assert api.create()[2] == first
    assert api.request("GET", first["status_url"] + "/matches")[2]["total"] == 1
    assert api.register("a")[0] == 201
    assert api.register("a", source=b"# changed\n")[0] == 409


def test_concurrent_creation_has_one_tournament_and_one_result(api):
    api.register("a")
    api.register("b")
    with ThreadPoolExecutor(max_workers=2) as executor:
        replies = list(executor.map(lambda _: api.create(), range(2)))
    assert [reply[0] for reply in replies] == [202, 202]
    assert replies[0][2] == replies[1][2]
    final = api.terminal(replies[0][2]["tournament_id"])
    assert final["statistics"]["attempts_total"] == 1


@pytest.mark.parametrize(
    "fields",
    [
        {"competitor_ids": ["a", "a"]},
        {"competitor_ids": ["a"]},
        {"first_player": True},
        {"seed": "42"},
        {"initial_piles": [False]},
        {"initial_piles": []},
        {"initial_piles": [0]},
        {"format": "unknown"},
        {"commands": ["sh"]},
        {"artifact_hashes": ["a" * 64, "b" * 64]},
    ],
)
def test_strict_body_validation(api, fields):
    status, _, data = api.request(
        "POST",
        "/api/v1/tournaments",
        body={
            "format": "elimination",
            "competitor_ids": ["a", "b"],
            **fields,
        },
        headers={"Idempotency-Key": "invalid"},
    )
    assert status == 422, data
    assert data["error"]["code"] == "invalid_request"


def test_missing_resource_and_missing_idempotency_key(api):
    assert api.create()[0] == 404
    assert api.request("GET", "/api/v1/tournaments/missing")[0] == 404
    assert api.request("POST", "/api/v1/tournaments/missing/cancel")[0] == 404
    assert api.request("GET", "/api/v1/tournaments/missing/matches")[0] == 404
    assert (
        api.request(
            "POST",
            "/api/v1/tournaments",
            body={
                "format": "elimination",
                "competitor_ids": ["a", "b"],
            },
        )[0]
        == 422
    )


def test_readiness_and_worker_information(api):
    assert api.request("GET", "/health")[0] == 200
    status, _, ready = api.request("GET", "/ready")
    assert status == 200
    assert all(check == "ready" for check in ready["checks"].values())
    status, _, workers = api.request("GET", "/api/v1/workers")
    assert status == 200
    assert workers == [
        {
            "schema_version": 1,
            "worker_id": "local",
            "status": "available",
            "capacity": 1,
            "running_attempts": 0,
        }
    ]
