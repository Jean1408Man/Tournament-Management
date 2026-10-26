"""Contrato OpenAPI versionado y reglas de cursor independientes de la red."""

import json
from pathlib import Path

import pytest

from tournament_management.api.errors import ApiError
from tournament_management.app import create_app
from tournament_management.config import Settings
from tournament_management.events.sse import parse_cursor

ROOT = Path(__file__).resolve().parents[2]


def test_versioned_openapi_matches_application():
    contract = json.loads((ROOT / "contracts" / "openapi-v1.json").read_text())
    assert contract == create_app(Settings()).openapi()
    assert contract["info"]["version"] == "1.0.0"
    upload = contract["paths"]["/api/v1/competitors"]["post"]
    assert (
        upload["requestBody"]["content"]["application/octet-stream"]["schema"]["format"] == "binary"
    )


@pytest.mark.parametrize("cursor", ["other:1", "t1:-1", "t1:1.0", "t1:１２", "t1:1\n", "t1:1:1"])
def test_event_cursor_is_scoped_and_unambiguous(cursor):
    with pytest.raises(ApiError):
        parse_cursor("t1", cursor, 0)


def test_event_cursor_and_after_cannot_be_combined():
    assert parse_cursor("t1", "t1:10", 0) == 10
    assert parse_cursor("t1", None, 4) == 4
    with pytest.raises(ApiError):
        parse_cursor("t1", "t1:10", 4)
