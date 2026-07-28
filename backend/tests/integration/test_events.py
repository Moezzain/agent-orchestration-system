"""Event bus tests — pub/sub roundtrip needs live Redis; graph emission
sequence is pure-unit (collector, no Redis)."""

import asyncio
from typing import Any
from uuid import uuid4

import pytest
from langchain_core.runnables import RunnableConfig

from orchestrator.config import Settings
from orchestrator.graph.build import build_graph
from orchestrator.graph.nodes import GraphDeps
from orchestrator.graph.state import initial_state
from orchestrator.memory import create_working_memory_client
from orchestrator.observability.events import (
    EventPublisher,
    EventType,
    TaskEvent,
    subscribe_events,
)
from orchestrator.schemas import Task
from tests.test_graph import FakeSpecialistRunner, FakeSupervisor, _accept_all, _chain_plan


async def test_pubsub_roundtrip_until_terminal_event() -> None:
    settings = Settings(_env_file=None)  # type: ignore[call-arg]
    client = create_working_memory_client(settings)
    try:
        await client.ping()
    except Exception:
        pytest.skip("redis not reachable — run `make infra-up`")

    task_id = uuid4()
    received: list[TaskEvent] = []

    async def consume() -> None:
        async for event in subscribe_events(client, task_id):
            received.append(event)

    consumer = asyncio.create_task(consume())
    await asyncio.sleep(0.1)  # let SUBSCRIBE land before publishing

    publisher = EventPublisher(client)
    await publisher.publish(
        TaskEvent(task_id=task_id, type=EventType.TASK_STARTED, data={"request": "x"})
    )
    await publisher.publish(
        TaskEvent(task_id=task_id, type=EventType.TASK_FINISHED, data={"has_output": True})
    )

    await asyncio.wait_for(consumer, timeout=5)
    await client.aclose()

    assert [e.type for e in received] == [EventType.TASK_STARTED, EventType.TASK_FINISHED]
    assert received[0].data == {"request": "x"}


async def test_graph_emits_progress_sequence() -> None:
    events: list[TaskEvent] = []

    async def collect(event: TaskEvent) -> None:
        events.append(event)

    deps = GraphDeps(
        supervisor=FakeSupervisor(_chain_plan()),
        reviewer=_accept_all("s1", "s2"),
        run_specialist=FakeSpecialistRunner(),
        emit=collect,
    )
    graph = build_graph(deps)
    config: RunnableConfig = {"configurable": {"thread_id": str(uuid4())}}
    task = Task(request="emit test")

    final: dict[str, Any] = await graph.ainvoke(initial_state(task), config)
    assert final["final_output"] is not None

    types = [e.type for e in events]
    assert types == [
        EventType.PLAN_CREATED,
        EventType.DISPATCHING,  # s1
        EventType.SUBTASK_COMPLETED,  # s1
        EventType.DISPATCHING,  # s2
        EventType.SUBTASK_COMPLETED,  # s2
        EventType.TASK_FINISHED,
    ]
    assert all(e.task_id == task.id for e in events)
    plan_event = events[0]
    assert [s["id"] for s in plan_event.data["subtasks"]] == ["s1", "s2"]
    completed = [e for e in events if e.type is EventType.SUBTASK_COMPLETED]
    assert completed[0].data["status"] == "accepted"
