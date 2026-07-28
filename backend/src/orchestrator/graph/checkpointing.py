"""Durable graph checkpoints in Postgres.

The checkpointer's tables (checkpoints, checkpoint_blobs, checkpoint_writes,
checkpoint_migrations) are LangGraph's own — created by setup(), excluded
from Alembic autogenerate (see alembic/env.py). They store opaque graph
state for pause/resume/replay; our domain tables remain the queryable
system of record. Two stores, two jobs.
"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver

from orchestrator.config import Settings

# tables the checkpointer owns — alembic/env.py must ignore these
CHECKPOINTER_TABLES = frozenset(
    {"checkpoints", "checkpoint_blobs", "checkpoint_writes", "checkpoint_migrations"}
)


def psycopg_dsn(settings: Settings) -> str:
    """Our app uses the asyncpg dialect; the checkpointer speaks psycopg."""
    return settings.database_url.replace("postgresql+asyncpg://", "postgresql://")


@asynccontextmanager
async def postgres_checkpointer(settings: Settings) -> AsyncIterator[AsyncPostgresSaver]:
    """Checkpointer with its schema ensured. setup() is idempotent (keeps its
    own migration version table) — call once per process, e.g. worker startup."""
    async with AsyncPostgresSaver.from_conn_string(psycopg_dsn(settings)) as saver:
        await saver.setup()
        yield saver
