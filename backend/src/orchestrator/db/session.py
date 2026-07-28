"""Engine and session factories.

Factory functions, not module-level singletons: an AsyncEngine binds to the
event loop it was created on. Celery worker processes and test loops must
each build their own engine inside their own loop.
"""

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from orchestrator.config import Settings


def create_engine(settings: Settings) -> AsyncEngine:
    return create_async_engine(settings.database_url, echo=False, pool_pre_ping=True)


def create_session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    # expire_on_commit=False: keep attribute values usable after commit,
    # instead of forcing a lazy refresh (which would need another await).
    return async_sessionmaker(engine, expire_on_commit=False)
