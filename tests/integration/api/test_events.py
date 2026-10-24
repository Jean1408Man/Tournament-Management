"""Reproducción y reconexión SSE contra historial confirmado."""

import json
from urllib.error import HTTPError

import pytest


def frames(response):
    current = {}
    for raw in response:
        line = raw.decode().rstrip("\r\n")
        if not line:
            if "data" in current:
                yield current
            current = {}
        elif ": " in line:
            key, value = line.split(": ", 1)
            current[key] = json.loads(value) if key == "data" else value


def test_sse_reconnect_excludes_confirmed_event_and_keeps_order(api):
    api.register("a")
    api.register("b")
    accepted = api.create()[2]
    identifier = accepted["tournament_id"]
    api.terminal(identifier)
    with api.sse(identifier) as stream:
        assert stream.headers["Content-Type"].startswith("text/event-stream")
        history = list(frames(stream))
    assert [frame["data"]["sequence"] for frame in history] == [1, 2, 3]
    assert history[-1]["event"] == "result.accepted"
    with api.sse(identifier, last=history[0]["id"]) as stream:
        tail = list(frames(stream))
    assert tail == history[1:]
    with api.sse(identifier, last=history[-1]["id"]) as stream:
        assert list(frames(stream)) == []
    with api.sse(identifier, after=1) as stream:
        assert list(frames(stream)) == history[1:]


def test_sse_disconnect_and_reconnect_while_tournament_is_running(api_factory):
    import threading

    from tournament_management.adapters.registered_execution import run_registered_match

    release = threading.Event()

    def delayed(attempt, store, cancel=None):
        release.wait(timeout=5)
        return run_registered_match(attempt, store, cancel=cancel)

    api = api_factory(runner=delayed)
    api.register("a")
    api.register("b")
    identifier = api.create()[2]["tournament_id"]
    with api.sse(identifier) as stream:
        first = next(frames(stream))
    release.set()
    api.terminal(identifier)
    with api.sse(identifier, last=first["id"]) as stream:
        remaining = list(frames(stream))
    assert remaining[-1]["event"] == "result.accepted"
    assert all(frame["data"]["sequence"] > first["data"]["sequence"] for frame in remaining)


def test_cursor_must_belong_to_tournament_and_confirmed_history(api):
    api.register("a")
    api.register("b")
    identifier = api.create()[2]["tournament_id"]
    api.terminal(identifier)
    for cursor, expected in [
        ("another:1", 400),
        (f"{identifier}:xyz", 400),
        (f"{identifier}:999999", 409),
    ]:
        with pytest.raises(HTTPError) as response:
            api.sse(identifier, last=cursor)
        assert response.value.code == expected
    with pytest.raises(HTTPError) as missing:
        api.sse("missing")
    assert missing.value.code == 404


def test_replay_spans_multiple_database_pages(api):
    from datetime import UTC, datetime
    from uuid import uuid4

    from tournament_management.domain.messages import DomainEvent

    api.register("a")
    api.register("b")
    identifier = api.create()[2]["tournament_id"]
    api.terminal(identifier)
    with api.app.state.services.work_factory() as work:
        sequence = work.events.next_sequence(identifier)
        for index in range(105):
            work.events.append(
                DomainEvent(
                    event_id=str(uuid4()),
                    tournament_id=identifier,
                    match_id="",
                    attempt_id="",
                    event_type="audit.test",
                    sequence=sequence + index,
                    occurred_at=datetime.now(UTC).isoformat(),
                    detail="pagination",
                )
            )
        work.commit()
    with api.sse(identifier) as response:
        history = list(frames(response))
    assert len(history) == 108
    assert [frame["data"]["sequence"] for frame in history] == list(range(1, 109))
