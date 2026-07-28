"""The golden path, end to end at worker level: submit -> pause for a human
-> approve -> resume -> complete. Real Postgres + Redis, scripted agents.

This is the system's signature flow — the test that proves HITL works
through every layer: graph gates, checkpoint persistence, approval rows,
resume segments, status lifecycle, and event emission.
"""

from collections.abc import AsyncIterator
from typing import Any
from uuid import UUID

import pytest

from orchestrator.config import Settings
from orchestrator.db import (
    ApprovalRepository,
    TaskRepository,
    create_engine,
    create_session_factory,
)
from orchestrator.graph.nodes import GraphDeps
from orchestrator.memory import WorkingMemory, create_working_memory_client
from orchestrator.observability.events import EventPublisher
from orchestrator.schemas import ApprovalDecision, ReviewVerdict, Task, TaskStatus
from orchestrator.worker.deps import RuntimeBundle
from orchestrator.worker.tasks import execute_task, resume_task
from tests.test_graph import FakeSpecialistRunner, FakeSupervisor, ScriptedReviewer, _chain_plan


@pytest.fixture
async def env() -> AsyncIterator[dict[str, Any]]:
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
        pytest.skip("infra not reachable — run `make infra-up`")

    task = Task(request="golden path: research then summarize")
    async with factory() as session:
        await TaskRepository(session).create(task)
        await session.commit()

    runner = FakeSpecialistRunner()
    # s1's review escalates -> pause; after human approval s2 accepts -> done
    reviewer = ScriptedReviewer({"s1": [ReviewVerdict.ESCALATE], "s2": [ReviewVerdict.ACCEPT]})

    def wm_factory(task_id: UUID) -> WorkingMemory:
        return WorkingMemory(wm_client, task_id)

    bundle = RuntimeBundle(
        deps=GraphDeps(
            supervisor=FakeSupervisor(_chain_plan()),
            reviewer=reviewer,
            run_specialist=runner,
        ),
        engine=engine,
        session_factory=factory,
        working_memory_factory=wm_factory,
        consolidator=None,
        publisher=EventPublisher(wm_client),
    )

    yield {"settings": settings, "bundle": bundle, "task": task, "runner": runner}

    async with factory() as session:
        from sqlalchemy import delete

        from orchestrator.db.models import ApprovalRow, SpanRow, TaskRow

        await session.execute(delete(SpanRow).where(SpanRow.task_id == task.id))
        await session.execute(delete(ApprovalRow).where(ApprovalRow.task_id == task.id))
        await session.execute(delete(TaskRow).where(TaskRow.id == task.id))
        await session.commit()
    await wm_client.aclose()
    await bundle.dispose()


async def test_golden_path_submit_escalate_approve_complete(env: dict[str, Any]) -> None:
    settings, bundle, task, runner = env["settings"], env["bundle"], env["task"], env["runner"]

    # ---- segment 1: run until the escalation pauses the task -------------
    output = await execute_task(task.id, settings, bundle=bundle)

    assert output is None  # paused, not finished
    async with bundle.session_factory() as session:
        stored = await TaskRepository(session).get(task.id)
        assert stored is not None and stored.status is TaskStatus.AWAITING_APPROVAL
        pending = await ApprovalRepository(session).list_pending(task.id)
    assert len(pending) == 1
    approval = pending[0]
    assert approval.subtask_id == "s1"
    assert approval.reason == "low_review_score"
    assert "s1" in (approval.context or "")
    # only s1 ran before the pause
    assert [sid for sid, _ in runner.runs] == ["s1"]

    # ---- the human decides ------------------------------------------------
    async with bundle.session_factory() as session:
        decided = await ApprovalRepository(session).decide(
            approval.id, ApprovalDecision.APPROVED, "output is fine — proceed"
        )
        await session.commit()
    assert decided is not None

    # ---- segment 2: resume from the durable checkpoint --------------------
    final_output = await resume_task(task.id, approval.id, settings, bundle=bundle)

    assert final_output is not None
    assert "s2 output" in final_output
    async with bundle.session_factory() as session:
        stored = await TaskRepository(session).get(task.id)
    assert stored is not None
    assert stored.status is TaskStatus.COMPLETED
    assert stored.result == final_output
    # s1 was NOT re-executed on resume (the footgun guarantee, worker-level)
    assert [sid for sid, _ in runner.runs] == ["s1", "s2"]

    # working memory cleared on completion
    wm = bundle.working_memory_factory(task.id)
    assert await wm.load_results() == {}
