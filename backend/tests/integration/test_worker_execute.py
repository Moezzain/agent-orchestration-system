"""execute_task integration: real Postgres + Redis, scripted agents, no LLM.

Covers the full worker lifecycle: PENDING -> RUNNING -> COMPLETED/FAILED,
checkpoints written, working memory populated then cleared.
"""

from collections.abc import AsyncIterator
from uuid import UUID, uuid4

import pytest

from orchestrator.config import Settings
from orchestrator.db import TaskRepository, create_engine, create_session_factory
from orchestrator.graph.nodes import GraphDeps
from orchestrator.memory import WorkingMemory, create_working_memory_client
from orchestrator.schemas import Task, TaskStatus
from orchestrator.worker.deps import RuntimeBundle
from orchestrator.worker.tasks import execute_task
from tests.test_graph import FakeSpecialistRunner, FakeSupervisor, _accept_all, _chain_plan


@pytest.fixture
async def env() -> AsyncIterator[tuple[Settings, RuntimeBundle, Task]]:
    settings = Settings(_env_file=None)  # type: ignore[call-arg]
    engine = create_engine(settings)
    factory = create_session_factory(engine)
    try:
        conn = await engine.connect()
        await conn.close()
        wm_client = create_working_memory_client(settings)
        await wm_client.ping()
    except Exception:
        await engine.dispose()
        pytest.skip("postgres/redis not reachable — run `make infra-up`")

    task = Task(request="integration run")
    async with factory() as session:
        await TaskRepository(session).create(task)
        await session.commit()

    def wm_factory(task_id: UUID) -> WorkingMemory:
        return WorkingMemory(wm_client, task_id)

    bundle = RuntimeBundle(
        deps=GraphDeps(
            supervisor=FakeSupervisor(_chain_plan()),
            reviewer=_accept_all("s1", "s2"),
            run_specialist=FakeSpecialistRunner(),
        ),
        engine=engine,
        session_factory=factory,
        working_memory_factory=wm_factory,
        consolidator=None,
    )

    yield settings, bundle, task

    async with factory() as session:
        from sqlalchemy import delete

        from orchestrator.db.models import TaskRow

        await session.execute(delete(TaskRow).where(TaskRow.id == task.id))
        await session.commit()
    await wm_client.aclose()
    await bundle.dispose()


async def test_full_lifecycle_completes_and_persists(
    env: tuple[Settings, RuntimeBundle, Task],
) -> None:
    settings, bundle, task = env

    output = await execute_task(task.id, settings, bundle=bundle)

    assert output is not None and "s2 output" in output
    async with bundle.session_factory() as session:
        stored = await TaskRepository(session).get(task.id)
    assert stored is not None
    assert stored.status is TaskStatus.COMPLETED
    assert stored.result == output

    # working memory was cleared on completion (task-scoped)
    wm = bundle.working_memory_factory(task.id)  # type: ignore[operator]
    assert await wm.load_results() == {}


async def test_missing_task_row_raises(env: tuple[Settings, RuntimeBundle, Task]) -> None:
    settings, bundle, _ = env

    with pytest.raises(ValueError, match="not found"):
        await execute_task(uuid4(), settings, bundle=bundle)


async def test_supervisor_blowup_marks_task_failed(
    env: tuple[Settings, RuntimeBundle, Task],
) -> None:
    settings, bundle, task = env

    class ExplodingSupervisor:
        async def plan(self, task: Task) -> object:
            raise RuntimeError("planner meltdown")

    broken = RuntimeBundle(
        deps=GraphDeps(
            supervisor=ExplodingSupervisor(),  # type: ignore[arg-type]
            reviewer=bundle.deps.reviewer,
            run_specialist=bundle.deps.run_specialist,
        ),
        engine=bundle.engine,
        session_factory=bundle.session_factory,
        working_memory_factory=bundle.working_memory_factory,
        consolidator=None,
    )

    with pytest.raises(RuntimeError, match="planner meltdown"):
        await execute_task(task.id, settings, bundle=broken)

    async with bundle.session_factory() as session:
        stored = await TaskRepository(session).get(task.id)
    assert stored is not None
    assert stored.status is TaskStatus.FAILED
    assert stored.result is not None and "planner meltdown" in stored.result
