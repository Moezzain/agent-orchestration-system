"""Fixtures for tests that need live infra (docker compose up).

Each test gets a session wrapped in an outer transaction that is rolled back
at the end — tests never leave rows behind and never see each other's data.
"""

from collections.abc import AsyncIterator

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from orchestrator.config import Settings
from orchestrator.db import create_engine, create_session_factory


@pytest.fixture
async def db_session() -> AsyncIterator[AsyncSession]:
    settings = Settings(_env_file=None)  # type: ignore[call-arg]
    engine = create_engine(settings)
    try:
        conn = await engine.connect()
    except OSError:
        pytest.skip("postgres not reachable — run `make infra-up`")

    txn = await conn.begin()
    factory = create_session_factory(engine)
    session = factory(bind=conn)
    try:
        yield session
    finally:
        await session.close()
        await txn.rollback()
        await conn.close()
        await engine.dispose()
