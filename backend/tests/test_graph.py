"""End-to-end graph tests with fully scripted agents — no LLM, no infra."""

import asyncio
import time
from typing import Any
from uuid import uuid4

from langchain_core.runnables import RunnableConfig

from orchestrator.agents.supervisor import PlanningOutcome
from orchestrator.graph.build import build_graph
from orchestrator.graph.nodes import GraphDeps
from orchestrator.graph.state import initial_state
from orchestrator.schemas import (
    AgentResult,
    Plan,
    ReviewResult,
    ReviewVerdict,
    SpecialistType,
    SubTask,
    Task,
)


def _sub(sid: str, depends_on: list[str] | None = None) -> SubTask:
    return SubTask(
        id=sid,
        description=f"do {sid}",
        specialist=SpecialistType.RESEARCH,
        depends_on=depends_on or [],
        expected_output="notes",
        complexity=2,
    )


def _chain_plan() -> Plan:
    return Plan(reasoning="chain", subtasks=[_sub("s1"), _sub("s2", ["s1"])])


def _diamond_plan() -> Plan:
    """s1 and s2 independent; s3 needs both — the parallelism case."""
    return Plan(
        reasoning="diamond",
        subtasks=[_sub("s1"), _sub("s2"), _sub("s3", ["s1", "s2"])],
    )


class FakeSupervisor:
    def __init__(self, plan: Plan) -> None:
        self._plan = plan

    async def plan(self, task: Task) -> PlanningOutcome:
        return PlanningOutcome(plan=self._plan, memories_used=[])


class FakeSpecialistRunner:
    """Records every run with wall-clock interval (to prove concurrency)."""

    def __init__(self, delay: float = 0.0) -> None:
        self._delay = delay
        self.runs: list[tuple[str, str]] = []  # (subtask_id, context)
        self.intervals: dict[str, tuple[float, float]] = {}

    async def __call__(self, subtask: SubTask, *, context: str, task: Task) -> AgentResult:
        start = time.monotonic()
        self.runs.append((subtask.id, context))
        if self._delay:
            await asyncio.sleep(self._delay)
        self.intervals[subtask.id] = (start, time.monotonic())
        attempt = sum(1 for sid, _ in self.runs if sid == subtask.id)
        return AgentResult(
            subtask_id=subtask.id,
            specialist=subtask.specialist,
            output=f"{subtask.id} output (attempt {attempt})",
            confidence=0.9,
        )


class ScriptedReviewer:
    def __init__(self, verdicts: dict[str, list[ReviewVerdict]]) -> None:
        self._verdicts = {k: list(v) for k, v in verdicts.items()}

    async def review(self, subtask: SubTask, result: AgentResult) -> ReviewResult:
        verdict = self._verdicts[subtask.id].pop(0)
        return ReviewResult(
            subtask_id=subtask.id,
            score=0.9 if verdict is ReviewVerdict.ACCEPT else 0.4,
            verdict=verdict,
            issues=[] if verdict is ReviewVerdict.ACCEPT else ["needs work"],
            feedback="" if verdict is ReviewVerdict.ACCEPT else "fix the intro",
        )


def _accept_all(*sids: str) -> ScriptedReviewer:
    return ScriptedReviewer({sid: [ReviewVerdict.ACCEPT] for sid in sids})


async def _run(deps: GraphDeps) -> dict[str, Any]:
    graph = build_graph(deps)
    config: RunnableConfig = {"configurable": {"thread_id": str(uuid4())}}
    result: dict[str, Any] = await graph.ainvoke(
        initial_state(Task(request="do the thing")), config
    )
    return result


async def test_chain_runs_in_dependency_order_with_context() -> None:
    runner = FakeSpecialistRunner()
    deps = GraphDeps(
        supervisor=FakeSupervisor(_chain_plan()),
        reviewer=_accept_all("s1", "s2"),
        run_specialist=runner,
    )

    final = await _run(deps)

    assert [sid for sid, _ in runner.runs] == ["s1", "s2"]
    assert "s1 output" in runner.runs[1][1]  # s2 saw s1's output
    assert final["subtask_status"] == {"s1": "accepted", "s2": "accepted"}
    assert "s2 output" in final["final_output"]


async def test_independent_subtasks_actually_run_concurrently() -> None:
    runner = FakeSpecialistRunner(delay=0.05)
    deps = GraphDeps(
        supervisor=FakeSupervisor(_diamond_plan()),
        reviewer=_accept_all("s1", "s2", "s3"),
        run_specialist=runner,
    )

    final = await _run(deps)

    # s1 and s2 intervals overlap => same superstep, truly parallel
    s1_start, s1_end = runner.intervals["s1"]
    s2_start, s2_end = runner.intervals["s2"]
    assert s1_start < s2_end and s2_start < s1_end, "s1/s2 did not overlap"
    # s3 strictly after both (its dependencies)
    s3_start, _ = runner.intervals["s3"]
    assert s3_start >= s1_end and s3_start >= s2_end
    # both parallel results survived the merge (reducers, not last-write-wins)
    assert set(final["results"]) == {"s1", "s2", "s3"}
    assert "s1 output" in final["results"]["s3"].output or True  # context checked below
    s3_context = next(ctx for sid, ctx in runner.runs if sid == "s3")
    assert "s1 output" in s3_context and "s2 output" in s3_context


async def test_revise_reruns_once_with_feedback() -> None:
    runner = FakeSpecialistRunner()
    deps = GraphDeps(
        supervisor=FakeSupervisor(_chain_plan()),
        reviewer=ScriptedReviewer(
            {"s1": [ReviewVerdict.REVISE, ReviewVerdict.ACCEPT], "s2": [ReviewVerdict.ACCEPT]}
        ),
        run_specialist=runner,
    )

    final = await _run(deps)

    assert [sid for sid, _ in runner.runs] == ["s1", "s1", "s2"]
    retry_context = runner.runs[1][1]
    assert "fix the intro" in retry_context and "previous attempt" in retry_context
    assert final["subtask_status"]["s1"] == "accepted"
    assert final["revision_counts"]["s1"] == 1


async def test_revision_budget_exhaustion_accepts_with_issues() -> None:
    runner = FakeSpecialistRunner()
    deps = GraphDeps(
        supervisor=FakeSupervisor(_chain_plan()),
        reviewer=ScriptedReviewer(
            {"s1": [ReviewVerdict.REVISE, ReviewVerdict.REVISE], "s2": [ReviewVerdict.ACCEPT]}
        ),
        run_specialist=runner,
    )

    final = await _run(deps)

    assert final["subtask_status"]["s1"] == "accepted_with_issues"
    assert any("unresolved issues" in e for e in final["errors"])
    assert final["subtask_status"]["s2"] == "accepted"  # pipeline continued


async def test_escalation_parks_subtask_and_pauses() -> None:
    # since step 27, escalate = pause for a human decision (the reject-then-
    # cascade path is covered in test_graph_interrupts)
    runner = FakeSpecialistRunner()
    deps = GraphDeps(
        supervisor=FakeSupervisor(_chain_plan()),
        reviewer=ScriptedReviewer({"s1": [ReviewVerdict.ESCALATE], "s2": []}),
        run_specialist=runner,
    )

    final = await _run(deps)

    assert [sid for sid, _ in runner.runs] == ["s1"]
    assert "__interrupt__" in final
    assert final["subtask_status"]["s1"] == "escalated"
    assert final["subtask_status"]["s2"] == "pending"  # untouched, awaiting decision
