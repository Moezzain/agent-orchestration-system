"""Registry middleware: invocation audit logging and per-tool rate limits.

Both are observability/policy code: they must never break tool execution.
Logging failures degrade to a warning; the rate limiter fails open if Redis
is unreachable.
"""

import time
from collections.abc import Mapping

import redis.asyncio as aioredis
from pydantic import BaseModel, ConfigDict
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from orchestrator.db.models import ToolInvocationRow
from orchestrator.logging import get_logger
from orchestrator.tools.base import InvocationContext, ToolExecutionResult
from orchestrator.tools.registry import CallNext

logger = get_logger(__name__)

_LOGGED_OUTPUT_CAP = 10_000


class ToolInvocationLogger:
    """Persists every invocation to tool_invocations — the tool audit trail."""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    async def __call__(
        self, context: InvocationContext, call_next: CallNext
    ) -> ToolExecutionResult:
        result = await call_next(context)
        try:
            async with self._session_factory() as session:
                session.add(
                    ToolInvocationRow(
                        task_id=context.task_id,
                        subtask_id=context.subtask_id,
                        tool_name=context.tool_name,
                        arguments=context.arguments,
                        result=result.output[:_LOGGED_OUTPUT_CAP],
                        success=result.success,
                        error=result.error,
                        latency_ms=result.latency_ms,
                    )
                )
                await session.commit()
        except Exception:  # noqa: BLE001 — audit logging must not break execution
            logger.warning("tool_invocation_log_failed", tool=context.tool_name, exc_info=True)
        return result


class RateLimit(BaseModel):
    model_config = ConfigDict(frozen=True)

    capacity: int  # bucket size = max burst
    refill_per_second: float  # sustained rate


# Atomic token bucket. Runs inside Redis, so concurrent workers can't race:
# read tokens -> refill by elapsed time -> take one if available.
_TOKEN_BUCKET_LUA = """
local key = KEYS[1]
local capacity = tonumber(ARGV[1])
local refill = tonumber(ARGV[2])
local now = tonumber(ARGV[3])
local state = redis.call('HMGET', key, 'tokens', 'ts')
local tokens = tonumber(state[1])
if tokens == nil then tokens = capacity end
local ts = tonumber(state[2])
if ts == nil then ts = now end
tokens = math.min(capacity, tokens + (now - ts) * refill)
local allowed = 0
if tokens >= 1 then
  tokens = tokens - 1
  allowed = 1
end
redis.call('HSET', key, 'tokens', tostring(tokens), 'ts', tostring(now))
redis.call('EXPIRE', key, 3600)
return allowed
"""


class ToolRateLimiter:
    """Token-bucket limits per tool, shared across processes via Redis.

    In-process counters wouldn't work: the API process and every Celery
    worker each have their own memory but must share one budget.
    """

    def __init__(
        self,
        redis_client: aioredis.Redis,
        limits: Mapping[str, RateLimit],
        *,
        key_prefix: str = "ratelimit",
    ) -> None:
        self._redis = redis_client
        self._limits = dict(limits)
        self._key_prefix = key_prefix
        self._script = redis_client.register_script(_TOKEN_BUCKET_LUA)

    async def __call__(
        self, context: InvocationContext, call_next: CallNext
    ) -> ToolExecutionResult:
        limit = self._limits.get(context.tool_name)
        if limit is None:
            return await call_next(context)

        try:
            allowed = await self._script(
                keys=[f"{self._key_prefix}:{context.tool_name}"],
                args=[limit.capacity, limit.refill_per_second, time.time()],
            )
        except Exception:  # noqa: BLE001 — fail open: no Redis, no limiting
            logger.warning("rate_limiter_unavailable", tool=context.tool_name, exc_info=True)
            return await call_next(context)

        if not allowed:
            return ToolExecutionResult(
                output="",
                success=False,
                error=(
                    f"rate limit exceeded for {context.tool_name!r} "
                    f"({limit.capacity} burst, {limit.refill_per_second}/s) — retry shortly"
                ),
                latency_ms=0,
            )
        return await call_next(context)
