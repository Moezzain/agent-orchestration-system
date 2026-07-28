"""Live task progress over Server-Sent Events.

Client contract (fetch-then-stream):
    1. GET /tasks/{id}           -> current durable state (Postgres)
    2. GET /tasks/{id}/stream    -> deltas from now on (Redis pub/sub)
The bus has no replay; anything before subscription is only in step 1's
snapshot. A `ping` comment goes out every 15s so proxies don't kill the
idle connection; the stream closes itself after a terminal event.

Why SSE over WebSocket: this feed is strictly one-directional; SSE is plain
HTTP (proxies, auth headers, HTTP/2 multiplexing all just work) and
EventSource reconnects natively. WebSocket buys bidirectionality we don't
need at the cost of an upgrade dance and hand-rolled reconnect.
"""

import asyncio
from collections.abc import AsyncIterator
from uuid import UUID

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse

from orchestrator.api.deps import RedisDep, SessionDep
from orchestrator.db import TaskRepository
from orchestrator.observability.events import TaskEvent, subscribe_events

router = APIRouter(prefix="/tasks", tags=["stream"])

_PING_INTERVAL_SECONDS = 15.0


def _sse(event: TaskEvent) -> str:
    # SSE wire format: named event + JSON data line + blank-line terminator
    return f"event: {event.type.value}\ndata: {event.model_dump_json()}\n\n"


async def _event_stream(redis, task_id: UUID) -> AsyncIterator[str]:  # type: ignore[no-untyped-def]
    queue: asyncio.Queue[TaskEvent | None] = asyncio.Queue()

    async def pump() -> None:
        try:
            async for event in subscribe_events(redis, task_id):
                await queue.put(event)
        finally:
            await queue.put(None)  # terminal event or subscriber failure -> close

    pump_task = asyncio.create_task(pump())
    try:
        while True:
            try:
                item = await asyncio.wait_for(queue.get(), timeout=_PING_INTERVAL_SECONDS)
            except TimeoutError:
                yield ": ping\n\n"  # SSE comment — keeps proxies from idling out
                continue
            if item is None:
                return
            yield _sse(item)
    finally:
        pump_task.cancel()


@router.get("/{task_id}/stream")
async def stream_task_events(
    task_id: UUID, session: SessionDep, redis: RedisDep
) -> StreamingResponse:
    task = await TaskRepository(session).get(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="task not found")

    return StreamingResponse(
        _event_stream(redis, task_id),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",  # tell nginx-ish proxies not to buffer
        },
    )
