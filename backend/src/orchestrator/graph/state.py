"""Graph state: the single shared object every node reads and partially updates.

Parallel fan-out means several work nodes finish in the same superstep, each
returning updates to the same dict keys. Without reducers, last-write-wins
would silently drop siblings' results — so every concurrently-written key
declares a merge reducer. Nodes return ONLY the entries they changed.
"""

import operator
from typing import Annotated, Any, TypedDict

from orchestrator.schemas import AgentResult, Escalation, Plan, ReviewResult, Task

# subtask lifecycle values in subtask_status
STATUS_PENDING = "pending"
STATUS_ACCEPTED = "accepted"
STATUS_ACCEPTED_WITH_ISSUES = "accepted_with_issues"
STATUS_FAILED = "failed"
STATUS_ESCALATED = "escalated"  # parked, awaiting a human decision at the gate

DONE_STATUSES = {STATUS_ACCEPTED, STATUS_ACCEPTED_WITH_ISSUES}


def merge_dicts(a: dict[str, Any], b: dict[str, Any]) -> dict[str, Any]:
    """Reducer: combine partial dict updates from parallel nodes."""
    return {**a, **b}


class OrchestratorState(TypedDict):
    """All keys required — initial_state() populates every one; nodes return
    partial plain-dict updates (which LangGraph merges via the reducers)."""

    task: Task
    plan: Plan | None
    # subtask ids runnable right now — written by dispatch, read by routing
    ready_ids: list[str]
    results: Annotated[dict[str, AgentResult], merge_dicts]
    reviews: Annotated[dict[str, ReviewResult], merge_dicts]
    revision_counts: Annotated[dict[str, int], merge_dicts]
    subtask_status: Annotated[dict[str, str], merge_dicts]
    final_output: str | None
    errors: Annotated[list[str], operator.add]
    # append-only escalation log; "pending" = its subtask is still ESCALATED
    escalations: Annotated[list[Escalation], operator.add]


def initial_state(task: Task) -> OrchestratorState:
    return OrchestratorState(
        task=task,
        plan=None,
        ready_ids=[],
        results={},
        reviews={},
        revision_counts={},
        subtask_status={},
        final_output=None,
        errors=[],
        escalations=[],
    )
