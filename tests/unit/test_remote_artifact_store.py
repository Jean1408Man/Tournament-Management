"""Descarga de bots por hash contra un servidor HTTP real (etapa 4.2)."""

import hashlib
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from tournament_management.competitors.artifact_store import ArtifactIntegrityError
from tournament_management.competitors.remote_store import RemoteArtifactStore
from tournament_management.execution.backends.registered import run_registered_match
from tournament_management.shared.messages import AttemptSpec, MatchSpec, OutcomeKind

ROOT = Path(__file__).resolve().parents[2]
BOT = (ROOT / "bots/optimal.py").read_bytes()
DIGEST = hashlib.sha256(BOT).hexdigest()
TOKEN = "secreto-de-prueba"


class Handler(BaseHTTPRequestHandler):
    mode = "ok"
    requests: list[str] = []

    def do_GET(self):  # noqa: N802 - nombre fijado por http.server
        Handler.requests.append(self.path)
        if self.headers.get("Authorization") != f"Bearer {TOKEN}":
            self.send_error(401)
            return
        if not self.path.endswith(DIGEST) or self.mode == "missing":
            self.send_error(404)
            return
        if self.mode == "slow":
            time.sleep(2)
        body = {"corrupt": BOT[:-1] + b"#", "big": BOT * 2}.get(self.mode, BOT)
        self.send_response(200)
        self.send_header("Content-Type", "application/octet-stream")
        # "truncated" anuncia todo el archivo pero solo envía la mitad y cierra.
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body[: len(body) // 2] if self.mode == "truncated" else body)

    def log_message(self, *args):
        pass


class QuietServer(ThreadingHTTPServer):
    def handle_error(self, request, client_address):
        pass  # El cliente cierra a propósito en "slow" y "truncated".


@pytest.fixture
def server():
    httpd = QuietServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    Handler.mode, Handler.requests = "ok", []
    yield f"http://127.0.0.1:{httpd.server_address[1]}"
    httpd.shutdown()
    httpd.server_close()


def store(url, *, token=TOKEN, max_bytes=None):
    size = len(BOT) if max_bytes is None else max_bytes
    return RemoteArtifactStore(url, token, max_bytes=size, timeout_ms=500)


def test_download_returns_verified_bytes(server):
    assert store(server).get(DIGEST) == BOT
    assert Handler.requests == [f"/internal/artifacts/{DIGEST}"]


@pytest.mark.parametrize("mode", ["truncated", "corrupt", "big", "missing", "slow"])
def test_every_bad_download_is_an_integrity_error(server, mode):
    Handler.mode = mode
    with pytest.raises(ArtifactIntegrityError):
        store(server).get(DIGEST)


def test_wrong_token_is_rejected(server):
    with pytest.raises(ArtifactIntegrityError):
        store(server, token="otro").get(DIGEST)


def test_size_limit_applies_before_hashing(server):
    with pytest.raises(ArtifactIntegrityError):
        store(server, max_bytes=len(BOT) - 1).get(DIGEST)


def test_malformed_hash_never_reaches_the_network(server):
    with pytest.raises(ValueError):
        store(server).get("../" + "a" * 61)
    assert Handler.requests == []


def test_unreachable_coordinator_is_an_integrity_error():
    with pytest.raises(ArtifactIntegrityError):
        store("http://127.0.0.1:9").get(DIGEST)


def test_configuration_is_required():
    for url, token in (("tm-coordinator:8000", TOKEN), ("http://tm-coordinator:8000", "")):
        with pytest.raises(ValueError):
            RemoteArtifactStore(url, token, max_bytes=1, timeout_ms=1)


def test_failed_download_is_infrastructure_without_starting_bots(server):
    Handler.mode = "truncated"
    attempt = AttemptSpec(
        attempt_id="a1", generation=1,
        match=MatchSpec(
            tournament_id="t", match_id="m", competitor_ids=("x", "y"),
            artifact_hashes=(DIGEST, DIGEST), initial_piles=(3,), first_player=0, seed=1,
        ),
        turn_timeout_ms=1000, match_timeout_ms=2000,
        memory_limit_bytes=1 << 30, output_limit_bytes=1 << 20,
    )
    outcome = run_registered_match(attempt, store(server), worker_id="tm-worker-1")
    assert outcome.kind is OutcomeKind.INFRASTRUCTURE_FAILURE
    assert (outcome.winner_id, outcome.worker_id) == ("", "tm-worker-1")
