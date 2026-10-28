"""Ruta interna de descarga de bots para los workers (4.2), con PostgreSQL y HTTP reales."""

import hashlib
import os
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest

from .client import ROOT

TOKEN = "token-de-prueba"


def download(api, digest, token=TOKEN):
    headers = {} if token is None else {"Authorization": f"Bearer {token}"}
    request = Request(f"{api.url}/internal/artifacts/{digest}", headers=headers)
    try:
        with urlopen(request, timeout=10) as response:
            return response.status, response.headers.get("Content-Type"), response.read()
    except HTTPError as error:
        with error:
            return error.status, error.headers.get("Content-Type"), error.read()


@pytest.fixture
def registered(api_factory):
    api = api_factory(internal_token=TOKEN)
    source = (ROOT / "bots" / "random_bot.py").read_bytes()
    assert api.register("a", source)[0] == 201
    return api, source, hashlib.sha256(source).hexdigest()


def test_registered_bot_downloads_with_token(registered):
    api, source, digest = registered
    status, content_type, body = download(api, digest)
    assert (status, content_type, body) == (200, "application/octet-stream", source)


@pytest.mark.parametrize("token", [None, "otro"])
def test_missing_or_wrong_token_is_401(registered, token):
    api, _, digest = registered
    assert download(api, digest, token)[0] == 401


def test_unknown_or_malformed_hash_is_404(registered):
    api, _, _ = registered
    assert download(api, "f" * 64)[0] == 404
    assert download(api, "no-es-un-hash")[0] == 404


def test_corrupted_file_on_coordinator_is_not_served(registered, tmp_path):
    api, source, digest = registered
    path = Path(tmp_path / "state" / "artifacts" / f"{digest}.py")
    os.chmod(path, 0o644)
    path.write_bytes(source[:-1] + b"#")
    assert download(api, digest)[0] == 503


def test_route_is_disabled_without_token(api_factory):
    api = api_factory()
    source = (ROOT / "bots" / "optimal.py").read_bytes()
    assert api.register("a", source)[0] == 201
    assert download(api, hashlib.sha256(source).hexdigest())[0] == 404


def test_internal_route_is_not_in_the_public_contract(api):
    status, _, schema = api.request("GET", "/api/v1/openapi.json")
    assert status == 200
    assert not any(path.startswith("/internal") for path in schema["paths"])
