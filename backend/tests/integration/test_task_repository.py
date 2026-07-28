from uuid import uuid4

from sqlalchemy.ext.asyncio import AsyncSession

from orchestrator.db import TaskRepository
from orchestrator.schemas import Task, TaskStatus


async def test_create_and_get_roundtrip(db_session: AsyncSession) -> None:
    repo = TaskRepository(db_session)
    task = Task(request="research quantum error correction")

    created = await repo.create(task)
    fetched = await repo.get(task.id)

    assert created.id == task.id
    assert fetched is not None
    assert fetched.request == task.request
    assert fetched.status == TaskStatus.PENDING
    assert fetched.created_at.tzinfo is not None  # timestamps come back tz-aware


async def test_set_status_updates_and_maps_back(db_session: AsyncSession) -> None:
    repo = TaskRepository(db_session)
    task = await repo.create(Task(request="write a summary"))

    updated = await repo.set_status(task.id, TaskStatus.COMPLETED, result="done: summary text")

    assert updated is not None
    assert updated.status == TaskStatus.COMPLETED
    assert updated.result == "done: summary text"


async def test_get_missing_returns_none(db_session: AsyncSession) -> None:
    repo = TaskRepository(db_session)

    assert await repo.get(uuid4()) is None


async def test_list_recent_returns_created_tasks(db_session: AsyncSession) -> None:
    # NOTE: Postgres now() is the TRANSACTION start time — rows inserted in one
    # transaction share created_at, so strict ordering can't be asserted here.
    repo = TaskRepository(db_session)
    first = await repo.create(Task(request="older"))
    second = await repo.create(Task(request="newer"))

    recent = await repo.list_recent(limit=10)
    ids = {t.id for t in recent}

    assert {first.id, second.id} <= ids
