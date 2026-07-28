"""Celery tasks: one task = one graph run OR one resume segment.

Pause protocol:
    graph hits interrupt() -> checkpoint persists -> ainvoke returns with
    __interrupt__ -> we write an approval row, mark AWAITING_APPROVAL, and the
    Celery task ENDS (no worker slot burned while a human thinks — the pause
    is durable in Postgres and survives restarts).
Resume protocol:
    human decides -> resume_graph_task(task_id, approval_id) -> graph.ainvoke(
    Command(resume=decision)) on the same thread_id -> continues from the
    checkpoint; may pause again on the next escalation, or run to completion.
"""

from collections.abc import Callable
from typing import Any
from uuid import UUID

from langchain_core.runnables import RunnableConfig
from langgraph.types import Command

from orchestrator.config import Settings, get_settings
from orchestrator.db import ApprovalRepository, TaskRepository
from orchestrator.graph.build import build_graph
from orchestrator.graph.checkpointing import postgres_checkpointer
from orchestrator.graph.state import initial_state
from orchestrator.logging import configure_logging, get_logger
from orchestrator.memory import WorkingMemory
from orchestrator.observability.events import EventType, TaskEvent
from orchestrator.observability.otel import force_flush, setup_tracing
from orchestrator.schemas import (
    ApprovalDecision,
    ApprovalDecisionInput,
    Escalation,
    TaskStatus,
)
from orchestrator.worker.celery_app import celery_app
from orchestrator.worker.deps import RuntimeBundle, build_runtime_bundle

logger = get_logger(__name__)


async def _publish(bundle: RuntimeBundle, event: TaskEvent) -> None:
    if bundle.publisher is not None:
        await bundle.publisher.publish(event)


async def _handle_interrupt(
    task_id: UUID, bundle: RuntimeBundle, final_state: dict[str, Any]
) -> None:
    """Persist the escalation as an approval row and park the task."""
    interrupts = final_state["__interrupt__"]
    escalation = Escalation.model_validate(interrupts[0].value)

    async with bundle.session_factory() as session:
        await ApprovalRepository(session).create_from_escalation(escalation)
        await TaskRepository(session).set_status(task_id, TaskStatus.AWAITING_APPROVAL)
        await session.commit()

    await _publish(
        bundle,
        TaskEvent(
            task_id=task_id,
            type=EventType.ESCALATION_RAISED,
            data={
                "approval_id": str(escalation.id),
                "reason": escalation.reason.value,
                "level": escalation.level.value,
                "proposed_action": escalation.proposed_action,
            },
        ),
    )
    logger.info("task_paused_for_approval", task_id=str(task_id), reason=escalation.reason.value)


async def _finalize(
    task_id: UUID,
    bundle: RuntimeBundle,
    final_state: dict[str, Any],
    working: WorkingMemory,
) -> str | None:
    """Terminal path: persist outputs, consolidate memory, clean up."""
    if final_state.get("plan") is not None:
        await working.save_plan(final_state["plan"])
    for result in final_state.get("results", {}).values():
        await working.save_result(result)
    for err in final_state.get("errors", []):
        await working.append_error(err)

    if bundle.consolidator is not None:
        async with bundle.session_factory() as session:
            task = await TaskRepository(session).get(task_id)
        if task is not None:
            try:
                await bundle.consolidator.consolidate(task, working)
            except Exception:
                logger.warning("consolidation_failed", task_id=str(task_id), exc_info=True)

    final_output = final_state.get("final_output")
    status = TaskStatus.COMPLETED if final_output else TaskStatus.FAILED
    async with bundle.session_factory() as session:
        await TaskRepository(session).set_status(task_id, status, result=final_output)
        await session.commit()

    await working.clear()
    return final_output


async def _run_segment(
    task_id: UUID,
    settings: Settings,
    bundle: RuntimeBundle,
    graph_input: Any,
) -> str | None:
    """One graph segment: from fresh start OR resume, until pause or the end."""
    working: WorkingMemory = bundle.working_memory_factory(task_id)  # type: ignore[operator]
    try:
        async with postgres_checkpointer(settings) as checkpointer:
            graph = build_graph(bundle.deps, checkpointer=checkpointer)
            config: RunnableConfig = {"configurable": {"thread_id": str(task_id)}}
            final_state: dict[str, Any] = await graph.ainvoke(graph_input, config)
        force_flush()  # spans land before the task reports its outcome

        if "__interrupt__" in final_state:
            await _handle_interrupt(task_id, bundle, final_state)
            return None
        return await _finalize(task_id, bundle, final_state, working)

    except Exception as exc:
        logger.error("task_execution_failed", task_id=str(task_id), exc_info=True)
        async with bundle.session_factory() as session:
            await TaskRepository(session).set_status(
                task_id, TaskStatus.FAILED, result=f"execution error: {exc}"
            )
            await session.commit()
        await _publish(
            bundle,
            TaskEvent(task_id=task_id, type=EventType.TASK_FAILED, data={"error": str(exc)}),
        )
        raise


async def execute_task(task_id: UUID, settings: Settings, *, bundle: RuntimeBundle) -> str | None:
    """Fresh run from the start."""
    async with bundle.session_factory() as session:
        task = await TaskRepository(session).get(task_id)
        if task is None:
            raise ValueError(f"task {task_id} not found — enqueue creates the row first")
        await TaskRepository(session).set_status(task_id, TaskStatus.RUNNING)
        await session.commit()

    await _publish(
        bundle,
        TaskEvent(task_id=task_id, type=EventType.TASK_STARTED, data={"request": task.request}),
    )
    return await _run_segment(task_id, settings, bundle, initial_state(task))


async def resume_task(
    task_id: UUID, approval_id: UUID, settings: Settings, *, bundle: RuntimeBundle
) -> str | None:
    """Continue a paused task with the human's recorded decision."""
    async with bundle.session_factory() as session:
        approval = await ApprovalRepository(session).get(approval_id)
        if approval is None or approval.decision is None:
            raise ValueError(f"approval {approval_id} not found or not yet decided")
        await TaskRepository(session).set_status(task_id, TaskStatus.RUNNING)
        await session.commit()
        decision = ApprovalDecisionInput(
            approved=approval.decision == ApprovalDecision.APPROVED.value,
            note=approval.decision_note or "",
        )

    await _publish(
        bundle,
        TaskEvent(
            task_id=task_id,
            type=EventType.TASK_RESUMED,
            data={"approval_id": str(approval_id), "approved": decision.approved},
        ),
    )
    return await _run_segment(
        task_id, settings, bundle, Command(resume=decision.model_dump(mode="json"))
    )


async def _run(task_id: UUID) -> str | None:
    settings = get_settings()
    bundle = await build_runtime_bundle(settings)
    try:
        return await execute_task(task_id, settings, bundle=bundle)
    finally:
        await bundle.dispose()


async def _resume(task_id: UUID, approval_id: UUID) -> str | None:
    settings = get_settings()
    bundle = await build_runtime_bundle(settings)
    try:
        return await resume_task(task_id, approval_id, settings, bundle=bundle)
    finally:
        await bundle.dispose()


# celery is untyped; the decorator would erase our signature
@celery_app.task(name="orchestrator.run_graph")  # type: ignore[untyped-decorator]
def run_graph_task(task_id: str) -> str | None:
    """Sync celery entrypoint; the entire async world lives inside asyncio.run."""
    import asyncio

    configure_logging()
    setup_tracing(get_settings())
    return asyncio.run(_run(UUID(task_id)))


@celery_app.task(name="orchestrator.resume_graph")  # type: ignore[untyped-decorator]
def resume_graph_task(task_id: str, approval_id: str) -> str | None:
    import asyncio

    configure_logging()
    setup_tracing(get_settings())
    return asyncio.run(_resume(UUID(task_id), UUID(approval_id)))


# typing aid for tests
WorkingMemoryFactory = Callable[[UUID], WorkingMemory]
