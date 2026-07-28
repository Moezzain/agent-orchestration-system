"""Escalation policy: WHEN does a human need to look?

Pure functions over data already in hand — no I/O, no LLM, trivially
unit-testable. The graph (step 27) acts on the returned Escalation by
interrupting; this module only decides.

Trigger → level mapping:
    reviewer says escalate      -> approve_action   (objective)
    repeated failure            -> approve_action   (objective)
    review score below floor    -> approve_action   (objective)
    self-confidence below floor -> approve_action   (weak signal, tunable)
    sensitive tool about to run -> approve_action   (objective)
    plan approval configured    -> approve_plan
    user asked for oversight    -> approve_plan

The objective triggers carry the system; self-reported confidence is known
to be poorly calibrated and its floor is set accordingly low.
"""

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from orchestrator.schemas import (
    AgentResult,
    ApprovalLevel,
    Escalation,
    EscalationReason,
    Plan,
    ReviewResult,
    ReviewVerdict,
    SubTask,
    Task,
)


class EscalationPolicy(BaseModel):
    """Thresholds are policy, not code — tune via Settings without redeploy."""

    model_config = ConfigDict(frozen=True)

    confidence_floor: float = Field(default=0.35, ge=0.0, le=1.0)
    review_score_floor: float = Field(default=0.40, ge=0.0, le=1.0)
    require_plan_approval: bool = False
    # Reasons demoted to NOTIFY: the run proceeds, the human is informed.
    # Default: the weak self-confidence signal informs; objective signals block.
    notify_reasons: frozenset[EscalationReason] = frozenset({EscalationReason.LOW_CONFIDENCE})

    def level_for(self, reason: EscalationReason) -> ApprovalLevel:
        if reason in self.notify_reasons:
            return ApprovalLevel.NOTIFY
        return ApprovalLevel.APPROVE_ACTION


def evaluate_plan(policy: EscalationPolicy, task: Task, plan: Plan) -> Escalation | None:
    """After planning, before any work starts."""
    if task.require_review:
        return Escalation(
            task_id=task.id,
            reason=EscalationReason.EXPLICIT_REQUEST,
            level=ApprovalLevel.APPROVE_PLAN,
            proposed_action=f"Execute a {len(plan.subtasks)}-subtask plan.",
            context=_plan_summary(plan),
        )
    if policy.require_plan_approval:
        return Escalation(
            task_id=task.id,
            reason=EscalationReason.EXPLICIT_REQUEST,
            level=ApprovalLevel.APPROVE_PLAN,
            proposed_action=(
                f"Execute a {len(plan.subtasks)}-subtask plan (policy requires sign-off)."
            ),
            context=_plan_summary(plan),
        )
    return None


def evaluate_result(
    policy: EscalationPolicy,
    task: Task,
    subtask: SubTask,
    result: AgentResult,
    review: ReviewResult,
) -> Escalation | None:
    """After a work unit settles (post-review, post-revisions). Priority:
    objective signals first, weak self-confidence signal last."""
    if review.verdict is ReviewVerdict.ESCALATE:
        return _result_escalation(
            policy,
            task,
            subtask,
            EscalationReason.LOW_REVIEW_SCORE,
            f"Reviewer refused to accept: {review.feedback or '; '.join(review.issues)}",
            result,
            review,
        )
    if result.error is not None:
        return _result_escalation(
            policy,
            task,
            subtask,
            EscalationReason.REPEATED_FAILURE,
            f"Specialist could not complete cleanly: {result.error}",
            result,
            review,
        )
    if review.score < policy.review_score_floor:
        return _result_escalation(
            policy,
            task,
            subtask,
            EscalationReason.LOW_REVIEW_SCORE,
            f"Review score {review.score:.2f} below floor {policy.review_score_floor:.2f}",
            result,
            review,
        )
    if result.confidence < policy.confidence_floor:
        return _result_escalation(
            policy,
            task,
            subtask,
            EscalationReason.LOW_CONFIDENCE,
            f"Self-reported confidence {result.confidence:.2f} below floor "
            f"{policy.confidence_floor:.2f}",
            result,
            review,
        )
    return None


def evaluate_tool(
    task_id: Any,
    *,
    subtask_id: str | None,
    tool_name: str,
    sensitive: bool,
    arguments: dict[str, Any],
) -> Escalation | None:
    """Before a tool executes. Only sensitive tools trigger."""
    if not sensitive:
        return None
    args_preview = str(arguments)[:500]
    return Escalation(
        task_id=task_id,
        subtask_id=subtask_id,
        reason=EscalationReason.SENSITIVE_TOOL,
        level=ApprovalLevel.APPROVE_ACTION,
        proposed_action=f"Run sensitive tool {tool_name!r}.",
        context=f"Arguments: {args_preview}",
    )


def _plan_summary(plan: Plan) -> str:
    lines = [f"Reasoning: {plan.reasoning}"]
    lines += [
        f"- {s.id} [{s.specialist.value}] {s.description}"
        + (f" (needs {', '.join(s.depends_on)})" if s.depends_on else "")
        for s in plan.subtasks
    ]
    return "\n".join(lines)


def _result_escalation(
    policy: EscalationPolicy,
    task: Task,
    subtask: SubTask,
    reason: EscalationReason,
    why: str,
    result: AgentResult,
    review: ReviewResult,
) -> Escalation:
    return Escalation(
        task_id=task.id,
        subtask_id=subtask.id,
        reason=reason,
        level=policy.level_for(reason),
        proposed_action=f"Accept, reject, or redirect the output of subtask {subtask.id!r}.",
        context=(
            f"Subtask: {subtask.description}\n"
            f"Why escalated: {why}\n"
            f"Review score {review.score:.2f}, confidence {result.confidence:.2f}\n"
            f"Output (excerpt): {result.output[:1500]}"
        ),
    )
