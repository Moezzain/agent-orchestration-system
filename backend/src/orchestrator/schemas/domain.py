"""Core domain contracts. Every layer types against these models.

All models are frozen: state changes produce new objects via
`model_copy(update={...})`, never in-place mutation. The Plan/SubTask models
double as LLM structured-output targets, so their field descriptions are
prompt material — write them for the model, not just for humans.
"""

from datetime import UTC, datetime
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, model_validator

from orchestrator.schemas.enums import (
    ApprovalLevel,
    EscalationReason,
    ReviewVerdict,
    SpecialistType,
    SubTaskStatus,
    TaskStatus,
)


def _utcnow() -> datetime:
    return datetime.now(UTC)


class DomainModel(BaseModel):
    model_config = ConfigDict(frozen=True)


class Task(DomainModel):
    """A user request as accepted by the system."""

    id: UUID = Field(default_factory=uuid4)
    request: str = Field(min_length=1)
    status: TaskStatus = TaskStatus.PENDING
    result: str | None = None
    # user explicitly wants human oversight -> plan approval before any work
    require_review: bool = False
    created_at: datetime = Field(default_factory=_utcnow)


class SubTask(DomainModel):
    """One unit of a plan, assigned to a single specialist.

    Structured-output target: the supervisor LLM produces these.
    """

    id: str = Field(
        description="Short unique id within the plan, e.g. 's1', 's2'.",
        pattern=r"^s\d+$",
    )
    description: str = Field(
        description="What the specialist must do, specific and self-contained."
    )
    specialist: SpecialistType = Field(description="Which specialist executes this subtask.")
    depends_on: list[str] = Field(
        default_factory=list,
        description="Ids of subtasks whose outputs this one needs. Empty if independent.",
    )
    expected_output: str = Field(description="Concrete description of the deliverable format.")
    complexity: int = Field(
        ge=1, le=5, description="Estimated complexity, 1 (trivial) to 5 (hard)."
    )
    status: SubTaskStatus = SubTaskStatus.PENDING


class Plan(DomainModel):
    """Supervisor's decomposition of a task. Structured-output target."""

    reasoning: str = Field(description="Why the task was decomposed this way.")
    subtasks: list[SubTask] = Field(min_length=1)

    @model_validator(mode="after")
    def _validate_dependencies(self) -> "Plan":
        ids = {s.id for s in self.subtasks}
        if len(ids) != len(self.subtasks):
            raise ValueError("subtask ids must be unique")

        for sub in self.subtasks:
            unknown = set(sub.depends_on) - ids
            if unknown:
                raise ValueError(f"subtask {sub.id!r} depends on unknown ids: {sorted(unknown)}")
            if sub.id in sub.depends_on:
                raise ValueError(f"subtask {sub.id!r} depends on itself")

        # Kahn's algorithm: if we can't consume every node, there's a cycle.
        remaining = {s.id: set(s.depends_on) for s in self.subtasks}
        while remaining:
            ready = [sid for sid, deps in remaining.items() if not deps]
            if not ready:
                raise ValueError(f"dependency cycle among subtasks: {sorted(remaining)}")
            for sid in ready:
                del remaining[sid]
            for deps in remaining.values():
                deps.difference_update(ready)
        return self

    def execution_batches(self) -> list[list[SubTask]]:
        """Subtasks grouped into dependency-ordered batches; each batch can run in parallel."""
        by_id = {s.id: s for s in self.subtasks}
        remaining = {s.id: set(s.depends_on) for s in self.subtasks}
        batches: list[list[SubTask]] = []
        while remaining:
            ready = sorted(sid for sid, deps in remaining.items() if not deps)
            batches.append([by_id[sid] for sid in ready])
            for sid in ready:
                del remaining[sid]
            for deps in remaining.values():
                deps.difference_update(ready)
        return batches


class AgentResult(DomainModel):
    """What a specialist returns after working a subtask."""

    subtask_id: str
    specialist: SpecialistType
    output: str
    confidence: float = Field(
        ge=0.0,
        le=1.0,
        description="Self-assessed confidence that the output meets the expected_output spec.",
    )
    error: str | None = None
    created_at: datetime = Field(default_factory=_utcnow)


class ReviewResult(DomainModel):
    """Reviewer's rubric-scored evaluation of an AgentResult. Structured-output target."""

    subtask_id: str
    score: float = Field(ge=0.0, le=1.0, description="Overall quality score.")
    verdict: ReviewVerdict
    issues: list[str] = Field(
        default_factory=list,
        description="Specific problems found; empty when verdict is accept.",
    )
    feedback: str = Field(
        default="",
        description="Actionable guidance for the specialist when verdict is revise.",
    )
    created_at: datetime = Field(default_factory=_utcnow)


class Escalation(DomainModel):
    """A decision point handed to the human operator."""

    id: UUID = Field(default_factory=uuid4)
    task_id: UUID
    subtask_id: str | None = None
    reason: EscalationReason
    level: ApprovalLevel
    proposed_action: str = Field(description="What the system wants to do next, in plain language.")
    context: str = Field(default="", description="Summary the human needs to decide.")
    created_at: datetime = Field(default_factory=_utcnow)


class ApprovalDecisionInput(DomainModel):
    """A human's answer to an escalation — the graph's resume payload."""

    approved: bool
    note: str = Field(default="", description="Optional guidance from the human.")
