"""Repositories: map between domain models (schemas/) and ORM rows (db/models.py).

Only the repository layer touches both worlds. Callers speak domain models;
storage shape stays swappable behind this boundary.
"""

from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from orchestrator.db.models import ApprovalRow, LLMCallRow, SpanRow, TaskRow
from orchestrator.llm.pricing import compute_cost_usd
from orchestrator.llm.types import LLMResponse
from orchestrator.schemas import (
    AgentRole,
    ApprovalDecision,
    Escalation,
    Task,
    TaskStatus,
)


def _to_domain(row: TaskRow) -> Task:
    return Task(
        id=row.id,
        request=row.request,
        status=TaskStatus(row.status),
        result=row.result,
        require_review=row.require_review,
        created_at=row.created_at,
    )


class TaskRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create(self, task: Task) -> Task:
        row = TaskRow(
            id=task.id,
            request=task.request,
            status=task.status.value,
            result=task.result,
            require_review=task.require_review,
        )
        self._session.add(row)
        await self._session.flush()
        return _to_domain(row)

    async def get(self, task_id: UUID) -> Task | None:
        row = await self._session.get(TaskRow, task_id)
        return _to_domain(row) if row else None

    async def set_status(
        self, task_id: UUID, status: TaskStatus, result: str | None = None
    ) -> Task | None:
        row = await self._session.get(TaskRow, task_id)
        if row is None:
            return None
        row.status = status.value
        if result is not None:
            row.result = result
        await self._session.flush()
        return _to_domain(row)

    async def list_recent(self, limit: int = 50) -> list[Task]:
        stmt = select(TaskRow).order_by(TaskRow.created_at.desc()).limit(limit)
        rows = await self._session.scalars(stmt)
        return [_to_domain(row) for row in rows]


class ApprovalRepository:
    """Approval queue rows — one per escalation the graph pauses on."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create_from_escalation(self, escalation: Escalation) -> UUID:
        row = ApprovalRow(
            id=escalation.id,
            task_id=escalation.task_id,
            subtask_id=escalation.subtask_id,
            reason=escalation.reason.value,
            level=escalation.level.value,
            proposed_action=escalation.proposed_action,
            context=escalation.context,
        )
        self._session.add(row)
        await self._session.flush()
        return row.id

    async def get(self, approval_id: UUID) -> ApprovalRow | None:
        return await self._session.get(ApprovalRow, approval_id)

    async def decide(
        self, approval_id: UUID, decision: ApprovalDecision, note: str = ""
    ) -> ApprovalRow | None:
        row = await self._session.get(ApprovalRow, approval_id)
        if row is None or row.decision is not None:
            return None  # unknown, or already decided (idempotency guard)
        row.decision = decision.value
        row.decision_note = note or None
        row.decided_at = datetime.now(UTC)
        await self._session.flush()
        return row

    async def list_pending(self, task_id: UUID | None = None) -> list[ApprovalRow]:
        stmt = select(ApprovalRow).where(ApprovalRow.decision.is_(None))
        if task_id is not None:
            stmt = stmt.where(ApprovalRow.task_id == task_id)
        rows = await self._session.scalars(stmt.order_by(ApprovalRow.created_at))
        return list(rows)


class LLMCallRepository:
    """Records every LLM call with its computed cost — the audit trail the
    trace explorer and cost dashboards read."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def record(
        self,
        response: LLMResponse,
        *,
        role: AgentRole,
        task_id: UUID | None = None,
    ) -> float:
        cost = compute_cost_usd(response.model, response.usage)
        self._session.add(
            LLMCallRow(
                task_id=task_id,
                agent_role=role.value,
                provider=response.provider,
                model=response.model,
                input_tokens=response.usage.input_tokens,
                output_tokens=response.usage.output_tokens,
                cost_usd=cost,
                latency_ms=response.latency_ms,
            )
        )
        await self._session.flush()
        return cost

    async def total_cost_for_task(self, task_id: UUID) -> float:
        stmt = select(func.coalesce(func.sum(LLMCallRow.cost_usd), 0.0)).where(
            LLMCallRow.task_id == task_id
        )
        return float(await self._session.scalar(stmt) or 0.0)

    async def cost_breakdown(self, task_id: UUID) -> list[dict[str, object]]:
        """Per (role, model): calls, tokens, cost — the trace explorer's table."""
        stmt = (
            select(
                LLMCallRow.agent_role,
                LLMCallRow.model,
                func.count().label("calls"),
                func.sum(LLMCallRow.input_tokens).label("input_tokens"),
                func.sum(LLMCallRow.output_tokens).label("output_tokens"),
                func.sum(LLMCallRow.cost_usd).label("cost_usd"),
            )
            .where(LLMCallRow.task_id == task_id)
            .group_by(LLMCallRow.agent_role, LLMCallRow.model)
            .order_by(func.sum(LLMCallRow.cost_usd).desc())
        )
        rows = (await self._session.execute(stmt)).all()
        return [
            {
                "agent_role": r.agent_role,
                "model": r.model,
                "calls": int(r.calls),
                "input_tokens": int(r.input_tokens or 0),
                "output_tokens": int(r.output_tokens or 0),
                "cost_usd": float(r.cost_usd or 0.0),
            }
            for r in rows
        ]


class SpanRepository:
    """Read side of the spans table (writer: PostgresSpanExporter)."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def list_for_task(self, task_id: UUID) -> list[SpanRow]:
        stmt = (
            select(SpanRow)
            .where(SpanRow.task_id == task_id)
            .order_by(SpanRow.started_at)
        )
        return list(await self._session.scalars(stmt))
