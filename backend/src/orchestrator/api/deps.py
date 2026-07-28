"""API dependency wiring.

Lifespan owns process-wide clients (engine, redis); Depends() hands out
request-scoped things (sessions). FastAPI DI is function-scoped composition —
same idea as Nest's injector, without classes or decorator modules.
"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Annotated, cast

import redis.asyncio as aioredis
from fastapi import Depends, FastAPI, Request
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from orchestrator.config import Settings, get_settings
from orchestrator.db import create_engine, create_session_factory
from orchestrator.logging import configure_logging
from orchestrator.memory import create_working_memory_client


@dataclass
class AppState:
    settings: Settings
    engine: AsyncEngine
    session_factory: async_sessionmaker[AsyncSession]
    redis: aioredis.Redis


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Create process-wide clients at startup, dispose at shutdown."""
    configure_logging()
    settings = get_settings()
    engine = create_engine(settings)
    state = AppState(
        settings=settings,
        engine=engine,
        session_factory=create_session_factory(engine),
        redis=create_working_memory_client(settings),
    )
    app.state.orchestrator = state
    try:
        yield
    finally:
        await state.redis.aclose()
        await engine.dispose()


def _app_state(request: Request) -> AppState:
    return cast(AppState, request.app.state.orchestrator)


async def get_session(request: Request) -> AsyncIterator[AsyncSession]:
    """One session per request; commit on success, rollback on exception."""
    state = _app_state(request)
    async with state.session_factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


def get_redis(request: Request) -> aioredis.Redis:
    return _app_state(request).redis


def get_app_settings(request: Request) -> Settings:
    return _app_state(request).settings


SessionDep = Annotated[AsyncSession, Depends(get_session)]
RedisDep = Annotated[aioredis.Redis, Depends(get_redis)]
SettingsDep = Annotated[Settings, Depends(get_app_settings)]
