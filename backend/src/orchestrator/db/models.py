"""ORM tables — the queryable system of record.

Deliberately separate from the Pydantic domain models in `schemas/`:
rows are storage shapes, domain models are contracts. Repositories map
between them at the boundary.

Enums are stored as plain strings: native Postgres ENUM types make every
new member an ALTER TYPE migration for zero query benefit at our scale.
"""

from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import DateTime, Float, ForeignKey, Integer, Text, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from orchestrator.db.base import Base


class TaskRow(Base):
    __tablename__ = "tasks"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    request: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(Text, index=True)
    result: Mapped[str | None] = mapped_column(Text)
    require_review: Mapped[bool] = mapped_column(default=False, server_default="false")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class PlanStepRow(Base):
    __tablename__ = "plan_steps"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    task_id: Mapped[UUID] = mapped_column(ForeignKey("tasks.id", ondelete="CASCADE"), index=True)
    subtask_id: Mapped[str] = mapped_column(Text)
    description: Mapped[str] = mapped_column(Text)
    specialist: Mapped[str] = mapped_column(Text)
    depends_on: Mapped[list[str]] = mapped_column(JSONB, default=list)
    expected_output: Mapped[str] = mapped_column(Text)
    complexity: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(Text, index=True)
    output: Mapped[str | None] = mapped_column(Text)
    confidence: Mapped[float | None] = mapped_column(Float)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ApprovalRow(Base):
    __tablename__ = "approvals"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    task_id: Mapped[UUID] = mapped_column(ForeignKey("tasks.id", ondelete="CASCADE"), index=True)
    subtask_id: Mapped[str | None] = mapped_column(Text)
    reason: Mapped[str] = mapped_column(Text)
    level: Mapped[str] = mapped_column(Text)
    proposed_action: Mapped[str] = mapped_column(Text)
    context: Mapped[str] = mapped_column(Text, default="")
    decision: Mapped[str | None] = mapped_column(Text, index=True)
    decision_note: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ToolInvocationRow(Base):
    __tablename__ = "tool_invocations"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    task_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("tasks.id", ondelete="SET NULL"), index=True
    )
    subtask_id: Mapped[str | None] = mapped_column(Text)
    tool_name: Mapped[str] = mapped_column(Text, index=True)
    arguments: Mapped[dict[str, object]] = mapped_column(JSONB, default=dict)
    result: Mapped[str | None] = mapped_column(Text)
    success: Mapped[bool] = mapped_column(default=True)
    error: Mapped[str | None] = mapped_column(Text)
    latency_ms: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class LLMCallRow(Base):
    __tablename__ = "llm_calls"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    task_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("tasks.id", ondelete="SET NULL"), index=True
    )
    agent_role: Mapped[str] = mapped_column(Text, index=True)
    provider: Mapped[str] = mapped_column(Text)
    model: Mapped[str] = mapped_column(Text, index=True)
    input_tokens: Mapped[int] = mapped_column(Integer)
    output_tokens: Mapped[int] = mapped_column(Integer)
    cost_usd: Mapped[float] = mapped_column(Float)
    latency_ms: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class SpanRow(Base):
    __tablename__ = "spans"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    task_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("tasks.id", ondelete="CASCADE"), index=True
    )
    parent_id: Mapped[UUID | None] = mapped_column()
    name: Mapped[str] = mapped_column(Text)
    kind: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(Text)
    attributes: Mapped[dict[str, object]] = mapped_column(JSONB, default=dict)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
