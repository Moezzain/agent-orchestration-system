"""Middleware integration tests — need live Redis + Postgres (make infra-up)."""

from collections.abc import AsyncIterator
from uuid import uuid4

import pytest
import redis.asyncio as aioredis
from pydantic import BaseModel, Field
from sqlalchemy import delete, select

from orchestrator.config import Settings
from orchestrator.db import create_engine, create_session_factory
from orchestrator.db.models import ToolInvocationRow
from orchestrator.tools import (
    InvocationContext,
    RateLimit,
    Tool,
    ToolInvocationLogger,
    ToolRateLimiter,
    ToolRegistry,
)


class PingArgs(BaseModel):
    value: str = Field(default="pong")


class PingTool(Tool[PingArgs]):
    name = "ping"
    description = "Returns its value."
    args_model = PingArgs

    async def execute(self, args: PingArgs) -> str:
        return args.value


@pytest.fixture
async def redis_client() -> AsyncIterator[aioredis.Redis]:
    settings = Settings(_env_file=None)  # type: ignore[call-arg]
    client = aioredis.from_url(settings.redis_url)
    try:
        await client.ping()
    except Exception:
        pytest.skip("redis not reachable — run `make infra-up`")
    yield client
    await client.aclose()


class TestRateLimiter:
    async def test_burst_capacity_then_denied(self, redis_client: aioredis.Redis) -> None:
        # unique prefix per test run — no state bleed between runs
        limiter = ToolRateLimiter(
            redis_client,
            {"ping": RateLimit(capacity=2, refill_per_second=0.0)},
            key_prefix=f"test:{uuid4().hex}",
        )
        registry = ToolRegistry(middlewares=[limiter])
        registry.register(PingTool())
        ctx = InvocationContext(tool_name="ping", arguments={})

        first = await registry.execute(ctx)
        second = await registry.execute(ctx)
        third = await registry.execute(ctx)

        assert first.success and second.success
        assert not third.success
        assert third.error is not None and "rate limit exceeded" in third.error

    async def test_unlimited_tool_passes_through(self, redis_client: aioredis.Redis) -> None:
        limiter = ToolRateLimiter(redis_client, {}, key_prefix=f"test:{uuid4().hex}")
        registry = ToolRegistry(middlewares=[limiter])
        registry.register(PingTool())

        result = await registry.execute(InvocationContext(tool_name="ping", arguments={}))

        assert result.success


class TestInvocationLogger:
    async def test_invocation_persisted_with_outcome(self) -> None:
        settings = Settings(_env_file=None)  # type: ignore[call-arg]
        engine = create_engine(settings)
        factory = create_session_factory(engine)
        try:
            conn = await engine.connect()
            await conn.close()
        except OSError:
            await engine.dispose()
            pytest.skip("postgres not reachable — run `make infra-up`")

        marker = uuid4().hex
        registry = ToolRegistry(middlewares=[ToolInvocationLogger(factory)])
        registry.register(PingTool())

        result = await registry.execute(
            InvocationContext(tool_name="ping", arguments={"value": marker})
        )
        assert result.success

        try:
            async with factory() as session:
                row = (
                    await session.scalars(
                        select(ToolInvocationRow).where(
                            ToolInvocationRow.arguments["value"].astext == marker
                        )
                    )
                ).one()
                assert row.tool_name == "ping"
                assert row.success is True
                assert row.result == marker
                assert row.latency_ms >= 0
        finally:
            async with factory() as session:
                await session.execute(
                    delete(ToolInvocationRow).where(
                        ToolInvocationRow.arguments["value"].astext == marker
                    )
                )
                await session.commit()
            await engine.dispose()
