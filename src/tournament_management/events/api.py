"""SSE desde PostgreSQL: relectura durable, páginas cortas y conexiones cerradas."""

import asyncio
import time
from typing import Annotated

from fastapi import APIRouter, Header, Query, Request
from fastapi.responses import StreamingResponse
from starlette.concurrency import run_in_threadpool

from tournament_management.api.common import Identifier
from tournament_management.api.dependencies import Services
from tournament_management.events.sse import encode, parse_cursor

router = APIRouter(tags=["events"])
TERMINAL = {"completed", "blocked", "cancelled"}


@router.get(
    "/tournaments/{tournament_id}/events",
    response_class=StreamingResponse,
    responses={
        200: {
            "content": {"text/event-stream": {"schema": {"type": "string"}}},
            "description": "Eventos confirmados, reanudables mediante Last-Event-ID",
        }
    },
)
async def events(
    tournament_id: Identifier,
    request: Request,
    runtime: Services,
    last_event_id: Annotated[str | None, Header(max_length=256)] = None,
    after: Annotated[int, Query(ge=0, le=10**18)] = 0,
):
    cursor = parse_cursor(tournament_id, last_event_id, after)
    # Validar existencia y cursor antes de enviar 200/cabeceras del stream.
    first = await run_in_threadpool(runtime.queries.events, tournament_id, cursor)

    async def stream():
        nonlocal cursor
        page = first
        heartbeat = time.monotonic()
        yield "retry: 1000\n\n"
        while not await request.is_disconnected():
            state, batch = page
            for event in batch:
                yield encode(event)
                cursor = event.sequence
                heartbeat = time.monotonic()
            if state in TERMINAL and len(batch) < 100:
                return
            if time.monotonic() - heartbeat >= 15:
                yield ": keepalive\n\n"
                heartbeat = time.monotonic()
            if len(batch) < 100:
                await asyncio.sleep(0.2)
            try:
                page = await run_in_threadpool(runtime.queries.events, tournament_id, cursor)
            except Exception:
                # Una desconexión del almacenamiento después de las cabeceras
                # no puede convertirse en HTTP 503. No avanzar el cursor.
                yield 'event: stream.error\ndata: {"code":"storage_unavailable"}\n\n'
                return

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )
