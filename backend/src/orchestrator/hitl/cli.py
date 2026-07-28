"""Approval queue CLI — the human side of HITL until the React UI exists.

Usage:
    uv run python -m orchestrator.hitl.cli list
    uv run python -m orchestrator.hitl.cli approve <approval_id> [note]
    uv run python -m orchestrator.hitl.cli reject <approval_id> [note]

Deciding records the decision in Postgres and enqueues the resume task.
"""

import asyncio
import sys
from uuid import UUID

from orchestrator.config import get_settings
from orchestrator.db import ApprovalRepository, create_engine, create_session_factory
from orchestrator.schemas import ApprovalDecision


async def _list() -> None:
    settings = get_settings()
    engine = create_engine(settings)
    factory = create_session_factory(engine)
    async with factory() as session:
        pending = await ApprovalRepository(session).list_pending()
    await engine.dispose()

    if not pending:
        print("no pending approvals")
        return
    for row in pending:
        print(f"approval {row.id}")
        print(f"  task     {row.task_id}  subtask {row.subtask_id or '-'}")
        print(f"  reason   {row.reason}  level {row.level}")
        print(f"  action   {row.proposed_action}")
        context_preview = (row.context or "")[:400]
        print(f"  context  {context_preview}")
        print()


async def _decide(approval_id: UUID, decision: ApprovalDecision, note: str) -> None:
    settings = get_settings()
    engine = create_engine(settings)
    factory = create_session_factory(engine)
    async with factory() as session:
        row = await ApprovalRepository(session).decide(approval_id, decision, note)
        await session.commit()
    await engine.dispose()

    if row is None:
        sys.exit(f"approval {approval_id} not found or already decided")

    # enqueue the resume segment (import here: celery app pulls in settings)
    from orchestrator.worker.tasks import resume_graph_task

    resume_graph_task.delay(str(row.task_id), str(approval_id))
    print(f"{decision.value}: task {row.task_id} resume enqueued")


def main() -> None:
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    command = sys.argv[1]
    if command == "list":
        asyncio.run(_list())
    elif command in ("approve", "reject"):
        if len(sys.argv) < 3:
            sys.exit("usage: approve|reject <approval_id> [note]")
        decision = ApprovalDecision.APPROVED if command == "approve" else ApprovalDecision.REJECTED
        note = sys.argv[3] if len(sys.argv) > 3 else ""
        asyncio.run(_decide(UUID(sys.argv[2]), decision, note))
    else:
        sys.exit(f"unknown command {command!r}\n{__doc__}")


if __name__ == "__main__":
    main()
