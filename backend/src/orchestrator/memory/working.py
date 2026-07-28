"""Task-scoped working memory in Redis.

The shared scratchpad for one task run: the plan, completed subtask results,
and an error log. Lives in Redis (not LangGraph state) because it must
outlive a single graph invocation — a paused task resumes in a different
Celery worker process, possibly days later — and because consolidation
(Phase 3) reads it after the graph is done.

Layout, three keys per task (constant-time cleanup):
    wm:{task_id}:plan     STRING  plan JSON
    wm:{task_id}:results  HASH    subtask_id -> AgentResult JSON
    wm:{task_id}:errors   LIST    error strings, append-only
All keys carry a TTL as a leak guard — a crashed task's memory expires
instead of accumulating forever.
"""

from uuid import UUID

import redis.asyncio as aioredis

from orchestrator.config import Settings, redis_url_with_db
from orchestrator.schemas import AgentResult, Plan

WORKING_MEMORY_DB = 2  # documented Redis DB map: /0 broker /1 results /2 wm /3 limits
_DEFAULT_TTL_SECONDS = 24 * 3600


def _as_str(value: bytes | str) -> str:
    # redis-py types everything bytes|str because decode_responses is a runtime
    # flag; our clients always decode, so this narrows for mypy
    return value.decode() if isinstance(value, bytes) else value


def create_working_memory_client(settings: Settings) -> aioredis.Redis:
    """Client pinned to the working-memory logical DB, whatever db the URL names."""
    return aioredis.from_url(
        redis_url_with_db(settings.redis_url, WORKING_MEMORY_DB), decode_responses=True
    )


class WorkingMemory:
    def __init__(
        self,
        redis_client: aioredis.Redis,
        task_id: UUID,
        *,
        ttl_seconds: int = _DEFAULT_TTL_SECONDS,
    ) -> None:
        self._redis = redis_client
        self._ttl = ttl_seconds
        prefix = f"wm:{task_id}"
        self._plan_key = f"{prefix}:plan"
        self._results_key = f"{prefix}:results"
        self._errors_key = f"{prefix}:errors"

    async def save_plan(self, plan: Plan) -> None:
        await self._redis.set(self._plan_key, plan.model_dump_json(), ex=self._ttl)

    async def load_plan(self) -> Plan | None:
        raw = await self._redis.get(self._plan_key)
        return Plan.model_validate_json(raw) if raw else None

    async def save_result(self, result: AgentResult) -> None:
        await self._redis.hset(self._results_key, result.subtask_id, result.model_dump_json())
        await self._redis.expire(self._results_key, self._ttl)

    async def load_result(self, subtask_id: str) -> AgentResult | None:
        raw = await self._redis.hget(self._results_key, subtask_id)
        return AgentResult.model_validate_json(raw) if raw else None

    async def load_results(self) -> dict[str, AgentResult]:
        raw = await self._redis.hgetall(self._results_key)
        return {_as_str(sid): AgentResult.model_validate_json(blob) for sid, blob in raw.items()}

    async def append_error(self, message: str) -> None:
        await self._redis.rpush(self._errors_key, message)
        await self._redis.expire(self._errors_key, self._ttl)

    async def errors(self) -> list[str]:
        return [_as_str(e) for e in await self._redis.lrange(self._errors_key, 0, -1)]

    async def clear(self) -> None:
        """Called when the task completes — working memory is task-scoped."""
        await self._redis.delete(self._plan_key, self._results_key, self._errors_key)
