"""Pause/resume semantics at graph level — InMemorySaver, scripted agents.

These pin the HITL contract: where the graph pauses, what payload the human
sees, and how their decision changes the run.
"""

from typing import Any
from uuid import uuid4

from langchain_core.runnables import RunnableConfig
from langgraph.types import Command

from orchestrator.graph.build import build_graph
from orchestrator.graph.nodes import GraphDeps
from orchestrator.graph.state import initial_state
from orchestrator.hitl import EscalationPolicy
from orchestrator.schemas import ReviewVerdict, Task
from tests.test_graph import (
    FakeSpecialistRunner,
    FakeSupervisor,
    ScriptedReviewer,
    _accept_all,
    _chain_plan,
)


def _config() -> RunnableConfig:
    return {"configurable": {"thread_id": str(uuid4())}}


async def test_reviewer_escalation_pauses_with_payload_then_approve_continues() -> None:
    runner = FakeSpecialistRunner()
    deps = GraphDeps(
        supervisor=FakeSupervisor(_chain_plan()),
        reviewer=ScriptedReviewer({"s1": [ReviewVerdict.ESCALATE], "s2": [ReviewVerdict.ACCEPT]}),
        run_specialist=runner,
    )
    graph = build_graph(deps)
    config = _config()

    paused: dict[str, Any] = await graph.ainvoke(initial_state(Task(request="pause test")), config)

    # paused, not finished: interrupt payload carries the escalation
    assert "__interrupt__" in paused
    payload = paused["__interrupt__"][0].value
    assert payload["subtask_id"] == "s1"
    assert payload["reason"] == "low_review_score"
    assert paused.get("final_output") is None
    assert [sid for sid, _ in runner.runs] == ["s1"]  # s2 not run yet

    resumed: dict[str, Any] = await graph.ainvoke(
        Command(resume={"approved": True, "note": "looks fine to me"}), config
    )

    # human accepted s1 -> pipeline continued to s2 and finished
    assert "__interrupt__" not in resumed
    assert resumed["subtask_status"]["s1"] == "accepted_with_issues"
    assert resumed["subtask_status"]["s2"] == "accepted"
    assert resumed["final_output"] is not None
    assert [sid for sid, _ in runner.runs] == ["s1", "s2"]


async def test_reject_fails_subtask_and_cascades() -> None:
    runner = FakeSpecialistRunner()
    deps = GraphDeps(
        supervisor=FakeSupervisor(_chain_plan()),
        reviewer=ScriptedReviewer({"s1": [ReviewVerdict.ESCALATE], "s2": []}),
        run_specialist=runner,
    )
    graph = build_graph(deps)
    config = _config()

    await graph.ainvoke(initial_state(Task(request="reject test")), config)
    resumed: dict[str, Any] = await graph.ainvoke(
        Command(resume={"approved": False, "note": "wrong direction"}), config
    )

    assert resumed["subtask_status"] == {"s1": "failed", "s2": "failed"}
    assert any("rejected by human (wrong direction)" in e for e in resumed["errors"])
    assert [sid for sid, _ in runner.runs] == ["s1"]  # s2 never ran


async def test_work_not_reexecuted_on_resume() -> None:
    """THE footgun test: the interrupt lives in the gate node, so resuming
    must NOT re-run the specialist or reviewer for the escalated subtask."""
    runner = FakeSpecialistRunner()
    reviewer = ScriptedReviewer({"s1": [ReviewVerdict.ESCALATE], "s2": [ReviewVerdict.ACCEPT]})
    deps = GraphDeps(
        supervisor=FakeSupervisor(_chain_plan()),
        reviewer=reviewer,
        run_specialist=runner,
    )
    graph = build_graph(deps)
    config = _config()

    await graph.ainvoke(initial_state(Task(request="idempotency test")), config)
    s1_runs_before = sum(1 for sid, _ in runner.runs if sid == "s1")

    await graph.ainvoke(Command(resume={"approved": True, "note": ""}), config)

    s1_runs_after = sum(1 for sid, _ in runner.runs if sid == "s1")
    assert s1_runs_before == s1_runs_after == 1  # no re-execution of the work


async def test_plan_approval_pauses_before_any_work() -> None:
    runner = FakeSpecialistRunner()
    deps = GraphDeps(
        supervisor=FakeSupervisor(_chain_plan()),
        reviewer=_accept_all("s1", "s2"),
        run_specialist=runner,
    )
    graph = build_graph(deps)
    config = _config()

    paused: dict[str, Any] = await graph.ainvoke(
        initial_state(Task(request="plan gate test", require_review=True)), config
    )

    assert "__interrupt__" in paused
    assert paused["__interrupt__"][0].value["level"] == "approve_plan"
    assert runner.runs == []  # nothing executed before the human saw the plan

    resumed: dict[str, Any] = await graph.ainvoke(
        Command(resume={"approved": True, "note": ""}), config
    )

    assert resumed["final_output"] is not None
    assert [sid for sid, _ in runner.runs] == ["s1", "s2"]


async def test_plan_rejection_runs_nothing() -> None:
    runner = FakeSpecialistRunner()
    deps = GraphDeps(
        supervisor=FakeSupervisor(_chain_plan()),
        reviewer=_accept_all("s1", "s2"),
        run_specialist=runner,
    )
    graph = build_graph(deps)
    config = _config()

    await graph.ainvoke(initial_state(Task(request="plan reject", require_review=True)), config)
    resumed: dict[str, Any] = await graph.ainvoke(
        Command(resume={"approved": False, "note": "not now"}), config
    )

    assert runner.runs == []
    assert resumed["subtask_status"] == {"s1": "failed", "s2": "failed"}
    assert any("plan rejected by human (not now)" in e for e in resumed["errors"])


async def test_low_confidence_result_triggers_pause() -> None:
    class LowConfidenceRunner(FakeSpecialistRunner):
        async def __call__(self, subtask, *, context, task):  # type: ignore[no-untyped-def]
            result = await super().__call__(subtask, context=context, task=task)
            return result.model_copy(update={"confidence": 0.05})

    deps = GraphDeps(
        supervisor=FakeSupervisor(_chain_plan()),
        reviewer=_accept_all("s1", "s2"),
        run_specialist=LowConfidenceRunner(),
        # since step 28 low confidence defaults to NOTIFY; empty notify_reasons
        # restores blocking behavior — this test pins that configurability
        policy=EscalationPolicy(confidence_floor=0.5, notify_reasons=frozenset()),
    )
    graph = build_graph(deps)
    config = _config()

    paused: dict[str, Any] = await graph.ainvoke(
        initial_state(Task(request="confidence test")), config
    )

    assert "__interrupt__" in paused
    assert paused["__interrupt__"][0].value["reason"] == "low_confidence"
