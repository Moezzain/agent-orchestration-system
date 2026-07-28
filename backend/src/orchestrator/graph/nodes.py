"""Graph nodes + routing.

Shape (parallel):

    supervise -> dispatch --Send x N--> work (specialist + review + revise, per subtask)
                    ^                     |
                    +---------------------+   (all Sends of a batch join here)
                    |
                    +-> synthesize -> END     (nothing runnable remains)

`work` is the atomic unit: run the specialist, review, revise at most once,
settle the subtask's status. Packing the revise loop inside the unit is what
makes fan-out clean — parallel branches never share per-subtask fields.
"""

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any, Literal, Protocol, TypedDict

from langgraph.types import Send, interrupt
from opentelemetry import trace

from orchestrator.agents.supervisor import PlanningOutcome
from orchestrator.graph.state import (
    DONE_STATUSES,
    STATUS_ACCEPTED,
    STATUS_ACCEPTED_WITH_ISSUES,
    STATUS_ESCALATED,
    STATUS_FAILED,
    STATUS_PENDING,
    OrchestratorState,
)
from orchestrator.hitl.policy import EscalationPolicy, evaluate_plan, evaluate_result
from orchestrator.observability.events import EventType, TaskEvent
from orchestrator.observability.otel import KIND_ATTR, TASK_ID_ATTR
from orchestrator.schemas import (
    AgentResult,
    ApprovalDecisionInput,
    ApprovalLevel,
    Plan,
    ReviewResult,
    ReviewVerdict,
    SubTask,
    Task,
)

MAX_REVISIONS = 1
_DEP_CONTEXT_CHARS = 4_000


class PlannerLike(Protocol):
    async def plan(self, task: Task) -> PlanningOutcome: ...


class ReviewerLike(Protocol):
    async def review(self, subtask: SubTask, result: AgentResult) -> ReviewResult: ...


class SpecialistRunner(Protocol):
    """Runs one subtask attempt. Production builds an AgentRuntime per call."""

    async def __call__(self, subtask: SubTask, *, context: str, task: Task) -> AgentResult: ...


# Progress sink — the worker wires this to the Redis event bus; None = silent
EventEmitter = Callable[[TaskEvent], Awaitable[None]]


@dataclass(frozen=True)
class GraphDeps:
    supervisor: PlannerLike
    reviewer: ReviewerLike
    run_specialist: SpecialistRunner
    emit: EventEmitter | None = None
    policy: EscalationPolicy = field(default_factory=EscalationPolicy)


class WorkItem(TypedDict):
    """Send payload: everything one work node needs, self-contained."""

    subtask: SubTask
    context: str
    task: Task


def _by_id(plan: Plan, subtask_id: str) -> SubTask:
    return next(s for s in plan.subtasks if s.id == subtask_id)


def _dependency_context(state: OrchestratorState, subtask: SubTask) -> str:
    parts = [
        f"# Output of dependency {dep_id}\n{state['results'][dep_id].output[:_DEP_CONTEXT_CHARS]}"
        for dep_id in subtask.depends_on
        if dep_id in state["results"]
    ]
    return "\n\n".join(parts)


def _revision_context(base: str, review: ReviewResult, prior: AgentResult) -> str:
    feedback = (
        f"# Your previous attempt was rejected by review\n"
        f"Feedback: {review.feedback}\n"
        f"Issues: {'; '.join(review.issues)}\n"
        f"# Your previous attempt\n{prior.output[:_DEP_CONTEXT_CHARS]}"
    )
    return f"{base}\n\n{feedback}" if base else feedback


_tracer = trace.get_tracer("orchestrator.graph")

NodeFn = Callable[[Any], Awaitable[dict[str, Any]]]


def _traced_node(name: str, fn: NodeFn) -> NodeFn:
    """Span per node execution. Interrupts propagate without error status
    (a pause is not a failure); task_id attr comes from state/payload."""

    async def wrapped(arg: Any) -> dict[str, Any]:
        task = arg.get("task") if isinstance(arg, dict) else None
        with _tracer.start_as_current_span(
            f"graph.{name}", record_exception=False, set_status_on_exception=False
        ) as span:
            span.set_attribute(KIND_ATTR, "graph_node")
            if task is not None:
                span.set_attribute(TASK_ID_ATTR, str(task.id))
            return await fn(arg)

    return wrapped


def make_nodes(deps: GraphDeps) -> dict[str, Any]:
    async def emit(state: OrchestratorState, type_: EventType, data: dict[str, Any]) -> None:
        if deps.emit is None:
            return
        await deps.emit(TaskEvent(task_id=state["task"].id, type=type_, data=data))

    async def supervise(state: OrchestratorState) -> dict[str, Any]:
        outcome = await deps.supervisor.plan(state["task"])
        await emit(
            state,
            EventType.PLAN_CREATED,
            {
                "reasoning": outcome.plan.reasoning,
                "subtasks": [
                    {"id": s.id, "specialist": s.specialist.value, "description": s.description}
                    for s in outcome.plan.subtasks
                ],
                "memories_used": len(outcome.memories_used),
            },
        )
        return {
            "plan": outcome.plan,
            "subtask_status": {s.id: STATUS_PENDING for s in outcome.plan.subtasks},
        }

    async def dispatch(state: OrchestratorState) -> dict[str, Any]:
        """Settle failure cascades, then compute the ready set (whole batch)."""
        plan = state["plan"]
        assert plan is not None  # supervise always runs first
        status = {**state["subtask_status"]}
        cascade_updates: dict[str, str] = {}
        errors: list[str] = []

        progressed = True
        while progressed:
            progressed = False
            for sub in plan.subtasks:
                if status[sub.id] != STATUS_PENDING:
                    continue
                if any(status[d] == STATUS_FAILED for d in sub.depends_on):
                    status[sub.id] = STATUS_FAILED
                    cascade_updates[sub.id] = STATUS_FAILED
                    errors.append(f"{sub.id} skipped: dependency failed")
                    progressed = True

        ready = [
            sub.id
            for sub in plan.subtasks
            if status[sub.id] == STATUS_PENDING
            and all(status[d] in DONE_STATUSES for d in sub.depends_on)
        ]
        if ready:
            await emit(state, EventType.DISPATCHING, {"ready_ids": ready})
        return {"ready_ids": ready, "subtask_status": cascade_updates, "errors": errors}

    async def work(item: WorkItem) -> dict[str, Any]:
        """One subtask, end to end: run -> review -> (revise once) -> settle."""
        sub, task, context = item["subtask"], item["task"], item["context"]

        result = await deps.run_specialist(sub, context=context, task=task)
        review = await deps.reviewer.review(sub, result)

        revisions = 0
        while review.verdict is ReviewVerdict.REVISE and revisions < MAX_REVISIONS:
            revisions += 1
            retry_context = _revision_context(context, review, result)
            result = await deps.run_specialist(sub, context=retry_context, task=task)
            review = await deps.reviewer.review(sub, result)

        errors: list[str] = []
        escalations = []
        escalation = evaluate_result(deps.policy, task, sub, result, review)
        if escalation is not None:
            # park the subtask; the gate node interrupts for a human decision
            status = STATUS_ESCALATED
            escalations.append(escalation)
        elif review.verdict is ReviewVerdict.ACCEPT:
            status = STATUS_ACCEPTED
        else:  # REVISE with budget exhausted
            status = STATUS_ACCEPTED_WITH_ISSUES
            errors.append(
                f"{sub.id} accepted with unresolved issues after {MAX_REVISIONS} "
                f"revision(s): {'; '.join(review.issues)}"
            )

        if deps.emit is not None:
            await deps.emit(
                TaskEvent(
                    task_id=task.id,
                    type=EventType.SUBTASK_COMPLETED,
                    data={
                        "subtask_id": sub.id,
                        "status": status,
                        "score": review.score,
                        "confidence": result.confidence,
                        "revisions": revisions,
                    },
                )
            )
        return {
            "results": {sub.id: result},
            "reviews": {sub.id: review},
            "revision_counts": {sub.id: revisions},
            "subtask_status": {sub.id: status},
            "errors": errors,
            "escalations": escalations,
        }

    async def plan_gate(state: OrchestratorState) -> dict[str, Any]:
        """Interrupt-isolated: no side effects before interrupt(). On resume
        this node re-executes from the top — evaluate_plan is pure, so the
        re-run is free; interrupt() then returns the human's decision instead
        of pausing again."""
        plan = state["plan"]
        assert plan is not None
        escalation = evaluate_plan(deps.policy, state["task"], plan)
        if escalation is None:
            return {}

        raw = interrupt(escalation.model_dump(mode="json"))
        decision = ApprovalDecisionInput.model_validate(raw)
        if decision.approved:
            return {"errors": []}
        # plan rejected: nothing runs; every subtask fails with the reason
        note = f" ({decision.note})" if decision.note else ""
        return {
            "subtask_status": {s.id: STATUS_FAILED for s in plan.subtasks},
            "errors": [f"plan rejected by human{note}"],
        }

    async def escalation_gate(state: OrchestratorState) -> dict[str, Any]:
        """Resolve parked subtasks, one interrupt per human decision.

        Re-execution semantics make the loop safe: on each resume the node
        reruns from the top, previously-answered interrupt() calls return
        their recorded values immediately, and only the next unanswered
        escalation actually pauses.
        """
        status_updates: dict[str, str] = {}
        errors: list[str] = []
        pending = [
            e
            for e in state["escalations"]
            if e.subtask_id is not None
            and state["subtask_status"].get(e.subtask_id) == STATUS_ESCALATED
        ]
        # NOTIFY level: proceed but inform — no interrupt, human sees an event
        blocking = []
        for escalation in pending:
            if escalation.level is ApprovalLevel.NOTIFY:
                assert escalation.subtask_id is not None
                status_updates[escalation.subtask_id] = STATUS_ACCEPTED_WITH_ISSUES
                errors.append(
                    f"{escalation.subtask_id} proceeded with notice: {escalation.reason.value}"
                )
                await emit(
                    state,
                    EventType.ESCALATION_RAISED,
                    {
                        "level": "notify",
                        "subtask_id": escalation.subtask_id,
                        "reason": escalation.reason.value,
                        "proposed_action": escalation.proposed_action,
                    },
                )
            else:
                blocking.append(escalation)

        for escalation in blocking:
            raw = interrupt(escalation.model_dump(mode="json"))
            decision = ApprovalDecisionInput.model_validate(raw)
            assert escalation.subtask_id is not None
            if decision.approved:
                status_updates[escalation.subtask_id] = STATUS_ACCEPTED_WITH_ISSUES
                errors.append(
                    f"{escalation.subtask_id} accepted by human despite: {escalation.reason.value}"
                )
            else:
                status_updates[escalation.subtask_id] = STATUS_FAILED
                note = f" ({decision.note})" if decision.note else ""
                errors.append(f"{escalation.subtask_id} rejected by human{note}")
        return {"subtask_status": status_updates, "errors": errors}

    async def synthesize(state: OrchestratorState) -> dict[str, Any]:
        plan = state["plan"]
        if plan is None:
            return {"final_output": None, "errors": ["no plan was produced"]}

        sections = [
            state["results"][sub.id].output
            for batch in plan.execution_batches()
            for sub in batch
            if state["subtask_status"].get(sub.id) in DONE_STATUSES
        ]
        header = ""
        if state["errors"]:
            header = "[completed with issues: " + "; ".join(state["errors"]) + "]\n\n"
        final = header + "\n\n---\n\n".join(sections) if sections else header or None
        await emit(
            state,
            EventType.TASK_FINISHED,
            {"has_output": final is not None, "issues": len(state["errors"])},
        )
        return {"final_output": final}

    nodes: dict[str, NodeFn] = {
        "supervise": supervise,
        "plan_gate": plan_gate,
        "dispatch": dispatch,
        "work": work,
        "escalation_gate": escalation_gate,
        "synthesize": synthesize,
    }
    return {name: _traced_node(name, fn) for name, fn in nodes.items()}


def route_after_dispatch(
    state: OrchestratorState,
) -> list[Send] | Literal["synthesize"]:
    """Fan the whole ready batch out as Send packets — one work node each."""
    ready = state["ready_ids"]
    if not ready:
        return "synthesize"
    plan = state["plan"]
    assert plan is not None
    return [
        Send(
            "work",
            WorkItem(
                subtask=_by_id(plan, sid),
                context=_dependency_context(state, _by_id(plan, sid)),
                task=state["task"],
            ),
        )
        for sid in ready
    ]
