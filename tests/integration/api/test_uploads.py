"""Límite de subida también sin Content-Length y ausencia de registros parciales."""

import http.client
import json
from urllib.parse import urlsplit


def test_invalid_python_and_media_type(api):
    assert api.register("broken", source=b"def broken(")[0] == 422
    assert api.request("GET", "/api/v1/competitors/broken")[0] == 404
    status, _, data = api.request(
        "POST",
        "/api/v1/competitors?name=Bot&filename=bot.py",
        data=b"# bot\n",
        headers={"Idempotency-Key": "wrong-type", "Content-Type": "text/plain"},
    )
    assert status == 415
    assert data["error"]["code"] == "unsupported_media_type"


def test_oversize_declared_and_chunked(api_factory):
    api = api_factory(artifact_max_bytes=32)
    assert api.register("large", source=b"#" * 33)[0] == 413
    address = urlsplit(api.url)
    connection = http.client.HTTPConnection(address.hostname, address.port, timeout=10)
    try:
        connection.request(
            "POST",
            "/api/v1/competitors?name=Large&filename=bot.py&competitor_id=chunked",
            body=iter([b"#" * 20, b"#" * 20]),
            encode_chunked=True,
            headers={"Content-Type": "application/octet-stream", "Idempotency-Key": "chunks"},
        )
        response = connection.getresponse()
        assert response.status == 413
        assert json.loads(response.read())["error"]["code"] == "upload_too_large"
    finally:
        connection.close()
    assert api.request("GET", "/api/v1/competitors/chunked")[0] == 404


def test_corrupted_artifact_prevents_tournament_creation(api):
    registered = api.register("a")[2]
    api.register("b")
    services = api.app.state.services
    digest = registered["artifact"]["artifact_hash"]
    path = services.competitors.store.directory / f"{digest}.py"
    path.chmod(0o600)
    path.write_bytes(b"# corrupted\n")
    assert api.request("GET", "/api/v1/competitors/a")[0] == 503
    status, _, error = api.create()
    assert status == 503
    assert error["error"]["code"] == "artifact_unavailable"


def test_disconnected_upload_does_not_register_partial_file(api):
    import socket
    import time

    address = urlsplit(api.url)
    with socket.create_connection((address.hostname, address.port), timeout=5) as connection:
        connection.sendall(
            b"POST /api/v1/competitors?name=Partial&filename=bot.py"
            b"&competitor_id=partial HTTP/1.1\r\n"
            b"Host: localhost\r\nContent-Type: application/octet-stream\r\n"
            b"Idempotency-Key: incomplete\r\nContent-Length: 1000\r\n\r\n# part"
        )
    time.sleep(0.1)
    assert api.request("GET", "/api/v1/competitors/partial")[0] == 404
