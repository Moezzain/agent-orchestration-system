"""SSE endpoint test — in-process ASGI + real Redis pub/sub end to end."""

import asyncio
import json
from collections.abc import AsyncIterator
from uuid import UUID

import pytest
from asgi_lifespan import LifespanManager
from httpx import ASGITransport, AsyncClient

from orchestrator.api.main import create_app
from orchestrator.config import Settings
from orchestrator.memory import create_working_memory_client
from orchestrator.observability.events import EventPublisher, EventType, TaskEvent


@pytest.fixture
async def client() -> AsyncIterator[AsyncClient]:
    settings = Settings(_env_file=None)  # type: ignore[call-arg]
    probe = create_working_memory_client(settings)
    try:
        await probe.ping()
    except Exception:
        pytest.skip("redis not reachable — run `make infra-up`")
    finally:
        await probe.aclose()

    app = create_app()
    async with (
        LifespanManager(app),
        AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as http,
    ):
        yield http


async def test_stream_delivers_events_and_closes_on_terminal(client: AsyncClient) -> None:
    created = (await client.post("/tasks", json={"request": "sse test"})).json()
    task_id = UUID(created["id"])

    settings = Settings(_env_file=None)  # type: ignore[call-arg]
    publisher_client = create_working_memory_client(settings)
    publisher = EventPublisher(publisher_client)

    received: list[tuple[str, dict[str, object]]] = []

    async def consume() -> None:
        async with client.stream("GET", f"/tasks/{task_id}/stream") as response:
            assert response.status_code == 200
            assert response.headers["content-type"].startswith("text/event-stream")
            event_name = None
            async for line in response.aiter_lines():
                if line.startswith("event: "):
                    event_name = line.removeprefix("event: ")
                elif line.startswith("data: ") and event_name:
                    received.append((event_name, json.loads(line.removeprefix("data: "))))

    consumer = asyncio.create_task(consume())
    await asyncio.sleep(0.3)  # let SUBSCRIBE land

    await publisher.publish(
        TaskEvent(task_id=task_id, type=EventType.DISPATCHING, data={"ready_ids": ["s1"]})
    )
    await publisher.publish(
        TaskEvent(task_id=task_id, type=EventType.TASK_FINISHED, data={"has_output": True})
    )

    # terminal event closes the stream server-side -> consumer finishes on its own
    await asyncio.wait_for(consumer, timeout=10)
    await publisher_client.aclose()

    assert [name for name, _ in received] == ["dispatching", "task_finished"]
    assert received[0][1]["data"] == {"ready_ids": ["s1"]}


async def test_stream_for_missing_task_404s(client: AsyncClient) -> None:
    from uuid import uuid4

    response = await client.get(f"/tasks/{uuid4()}/stream")
    assert response.status_code == 404
