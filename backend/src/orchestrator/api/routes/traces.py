"""Trace endpoints: span tree + cost breakdown for one task."""

from datetime import datetime
from typing import Any
from uuid import UUID

from fastapi import APIRouter
from pydantic import BaseModel

from orchestrator.api.deps import SessionDep
from orchestrator.db import LLMCallRepository, SpanRepository
from orchestrator.db.models import SpanRow

router = APIRouter(prefix="/tasks/{task_id}", tags=["traces"])


class SpanResponse(BaseModel):
    id: UUID
    parent_id: UUID | None
    name: str
    kind: str
    status: str
    attributes: dict[str, Any]
    started_at: datetime
    ended_at: datetime | None
    duration_ms: int

    @classmethod
    def from_row(cls, row: SpanRow) -> "SpanResponse":
        ended = row.ended_at or row.started_at
        return cls(
            id=row.id,
            parent_id=row.parent_id,
            name=row.name,
            kind=row.kind,
            status=row.status,
            attributes=row.attributes,
            started_at=row.started_at,
            ended_at=row.ended_at,
            duration_ms=int((ended - row.started_at).total_seconds() * 1000),
        )


class CostRow(BaseModel):
    agent_role: str
    model: str
    calls: int
    input_tokens: int
    output_tokens: int
    cost_usd: float


class CostReport(BaseModel):
    total_usd: float
    rows: list[CostRow]


@router.get("/spans", response_model=list[SpanResponse])
async def list_spans(task_id: UUID, session: SessionDep) -> list[SpanResponse]:
    rows = await SpanRepository(session).list_for_task(task_id)
    return [SpanResponse.from_row(r) for r in rows]


@router.get("/costs", response_model=CostReport)
async def cost_report(task_id: UUID, session: SessionDep) -> CostReport:
    repo = LLMCallRepository(session)
    breakdown = await repo.cost_breakdown(task_id)
    total = await repo.total_cost_for_task(task_id)
    return CostReport(total_usd=total, rows=[CostRow(**r) for r in breakdown])  # type: ignore[arg-type]
