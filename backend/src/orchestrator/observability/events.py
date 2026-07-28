"""Task event bus: Redis pub/sub, one channel per task.

Why this exists: the graph runs inside a Celery worker process; the API
process (SSE, step 30) and the CLI tail cannot see into it. Nodes publish
progress here; anyone interested subscribes to task:{id}:events.

Note: Redis pub/sub channels are SERVER-global — logical DBs don't namespace
them (a classic surprise). Channel names carry the task id, so that's fine.
Pub/sub is also fire-and-forget: subscribers who connect late miss earlier
events. Durable history lives in Postgres; this bus is for liveness only.
"""

from collections.abc import AsyncIterator
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any
from uuid import UUID

import redis.asyncio as aioredis
from pydantic import BaseModel, ConfigDict, Field

from orchestrator.logging import get_logger

logger = get_logger(__name__)


class EventType(StrEnum):
    TASK_STARTED = "task_started"
    PLAN_CREATED = "plan_created"
    DISPATCHING = "dispatching"
    SUBTASK_COMPLETED = "subtask_completed"
    ESCALATION_RAISED = "escalation_raised"  # paused, awaiting a human
    TASK_RESUMED = "task_resumed"
    TASK_FINISHED = "task_finished"
    TASK_FAILED = "task_failed"


class TaskEvent(BaseModel):
    model_config = ConfigDict(frozen=True)

    task_id: UUID
    type: EventType
    data: dict[str, Any] = Field(default_factory=dict)
    ts: str = Field(default_factory=lambda: datetime.now(UTC).isoformat())


def channel_for(task_id: UUID) -> str:
    return f"task:{task_id}:events"


class EventPublisher:
    """Fire-and-forget publisher — progress reporting must never break a run."""

    def __init__(self, redis_client: aioredis.Redis) -> None:
        self._redis = redis_client

    async def publish(self, event: TaskEvent) -> None:
        try:
            await self._redis.publish(channel_for(event.task_id), event.model_dump_json())
        except Exception:  # noqa: BLE001
            logger.warning("event_publish_failed", type=event.type.value, exc_info=True)


async def subscribe_events(redis_client: aioredis.Redis, task_id: UUID) -> AsyncIterator[TaskEvent]:
    """Yield events for one task until the subscriber is cancelled or a
    terminal event (task_finished / task_failed) arrives."""
    pubsub = redis_client.pubsub()
    await pubsub.subscribe(channel_for(task_id))
    try:
        async for message in pubsub.listen():
            if message["type"] != "message":
                continue
            event = TaskEvent.model_validate_json(message["data"])
            yield event
            if event.type in (EventType.TASK_FINISHED, EventType.TASK_FAILED):
                return
    finally:
        await pubsub.unsubscribe(channel_for(task_id))
        # redis-py typing gap on PubSub.aclose
        await pubsub.aclose()  # type: ignore[no-untyped-call]
