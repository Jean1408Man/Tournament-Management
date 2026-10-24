"""Cliente HTTP estándar reutilizable entre servidores de hilo y de proceso."""

import json
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

import pytest

ROOT = Path(__file__).resolve().parents[3]


@dataclass
class ApiClient:
    url: str
    app: object = None
    stop: Callable[[], None] | None = None

    def close(self):
        if self.stop is not None:
            self.stop()

    def request(self, method, path, *, body=None, data=None, headers=None):
        headers = dict(headers or {})
        if body is not None:
            data = json.dumps(body).encode()
            headers["Content-Type"] = "application/json"
        request = Request(self.url + path, data=data, headers=headers, method=method)
        try:
            response = urlopen(request, timeout=10)
        except HTTPError as error:
            response = error
        with response:
            payload = response.read()
            return response.status, response.headers, json.loads(payload) if payload else None

    def register(self, identifier, source=None, key=None):
        if source is None:
            source = (ROOT / "bots" / "optimal.py").read_bytes()
        query = urlencode(
            {"name": identifier, "filename": "new-bot.py", "competitor_id": identifier}
        )
        return self.request(
            "POST",
            "/api/v1/competitors?" + query,
            data=source,
            headers={
                "Content-Type": "application/octet-stream",
                "Idempotency-Key": key or identifier,
            },
        )

    def create(self, *, format_name="elimination", identifiers=("a", "b"), key="t1", **fields):
        return self.request(
            "POST",
            "/api/v1/tournaments",
            body={
                "format": format_name,
                "competitor_ids": list(identifiers),
                **fields,
            },
            headers={"Idempotency-Key": key},
        )

    def terminal(self, tournament_id):
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            status, _, data = self.request("GET", f"/api/v1/tournaments/{tournament_id}")
            assert status == 200, data
            if data["state"] in {"completed", "blocked", "cancelled"}:
                return data
            time.sleep(0.05)
        pytest.fail("El torneo no alcanzó un estado terminal")

    def sse(self, tournament_id, *, last=None, after=None):
        path = f"/api/v1/tournaments/{tournament_id}/events"
        if after is not None:
            path += f"?after={after}"
        headers = {"Last-Event-ID": last} if last is not None else {}
        return urlopen(Request(self.url + path, headers=headers), timeout=10)
