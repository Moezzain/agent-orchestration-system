"""Approval queue endpoints: list pending, decide (decide -> enqueue resume)."""

from datetime import datetime
from uuid import UUID

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from orchestrator.api.deps import SessionDep
from orchestrator.db import ApprovalRepository
from orchestrator.db.models import ApprovalRow
from orchestrator.schemas import ApprovalDecision

router = APIRouter(prefix="/approvals", tags=["approvals"])


class ApprovalResponse(BaseModel):
    id: UUID
    task_id: UUID
    subtask_id: str | None
    reason: str
    level: str
    proposed_action: str
    context: str
    decision: str | None
    created_at: datetime

    @classmethod
    def from_row(cls, row: ApprovalRow) -> "ApprovalResponse":
        return cls(
            id=row.id,
            task_id=row.task_id,
            subtask_id=row.subtask_id,
            reason=row.reason,
            level=row.level,
            proposed_action=row.proposed_action,
            context=row.context or "",
            decision=row.decision,
            created_at=row.created_at,
        )


class DecideRequest(BaseModel):
    approved: bool
    note: str = Field(default="", max_length=2_000)


@router.get("", response_model=list[ApprovalResponse])
async def list_pending(session: SessionDep, task_id: UUID | None = None) -> list[ApprovalResponse]:
    rows = await ApprovalRepository(session).list_pending(task_id)
    return [ApprovalResponse.from_row(r) for r in rows]


@router.post("/{approval_id}/decision", response_model=ApprovalResponse)
async def decide(approval_id: UUID, body: DecideRequest, session: SessionDep) -> ApprovalResponse:
    decision = ApprovalDecision.APPROVED if body.approved else ApprovalDecision.REJECTED
    row = await ApprovalRepository(session).decide(approval_id, decision, body.note)
    if row is None:
        raise HTTPException(status_code=409, detail="approval not found or already decided")
    await session.commit()  # decision durable before the resume can read it

    from orchestrator.worker.tasks import resume_graph_task

    resume_graph_task.delay(str(row.task_id), str(approval_id))
    return ApprovalResponse.from_row(row)
