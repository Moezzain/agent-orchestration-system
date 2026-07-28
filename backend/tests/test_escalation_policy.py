from uuid import uuid4

from orchestrator.hitl import (
    EscalationPolicy,
    evaluate_plan,
    evaluate_result,
    evaluate_tool,
)
from orchestrator.schemas import (
    AgentResult,
    ApprovalLevel,
    EscalationReason,
    Plan,
    ReviewResult,
    ReviewVerdict,
    SpecialistType,
    SubTask,
    Task,
)

POLICY = EscalationPolicy()  # defaults: conf 0.35, score 0.40


def _task(require_review: bool = False) -> Task:
    return Task(request="do the thing", require_review=require_review)


def _subtask() -> SubTask:
    return SubTask(
        id="s1",
        description="research X",
        specialist=SpecialistType.RESEARCH,
        expected_output="notes",
        complexity=2,
    )


def _plan() -> Plan:
    return Plan(reasoning="one step", subtasks=[_subtask()])


def _result(confidence: float = 0.8, error: str | None = None) -> AgentResult:
    return AgentResult(
        subtask_id="s1",
        specialist=SpecialistType.RESEARCH,
        output="fine output",
        confidence=confidence,
        error=error,
    )


def _review(score: float = 0.8, verdict: ReviewVerdict = ReviewVerdict.ACCEPT) -> ReviewResult:
    return ReviewResult(subtask_id="s1", score=score, verdict=verdict, feedback="meh")


class TestResultTriggers:
    def test_clean_result_no_escalation(self) -> None:
        assert evaluate_result(POLICY, _task(), _subtask(), _result(), _review()) is None

    def test_reviewer_escalate_verdict(self) -> None:
        esc = evaluate_result(
            POLICY, _task(), _subtask(), _result(), _review(0.3, ReviewVerdict.ESCALATE)
        )
        assert esc is not None
        assert esc.reason is EscalationReason.LOW_REVIEW_SCORE
        assert esc.level is ApprovalLevel.APPROVE_ACTION
        assert esc.subtask_id == "s1"

    def test_result_error_means_repeated_failure(self) -> None:
        esc = evaluate_result(
            POLICY,
            _task(),
            _subtask(),
            _result(error="turn budget exhausted after 8 turns"),
            _review(),
        )
        assert esc is not None
        assert esc.reason is EscalationReason.REPEATED_FAILURE

    def test_low_review_score(self) -> None:
        esc = evaluate_result(POLICY, _task(), _subtask(), _result(), _review(score=0.2))
        assert esc is not None
        assert esc.reason is EscalationReason.LOW_REVIEW_SCORE

    def test_low_confidence(self) -> None:
        esc = evaluate_result(POLICY, _task(), _subtask(), _result(confidence=0.1), _review())
        assert esc is not None
        assert esc.reason is EscalationReason.LOW_CONFIDENCE

    def test_priority_objective_beats_weak_signal(self) -> None:
        # error AND low confidence: repeated failure (objective) wins
        esc = evaluate_result(
            POLICY, _task(), _subtask(), _result(confidence=0.1, error="boom"), _review()
        )
        assert esc is not None
        assert esc.reason is EscalationReason.REPEATED_FAILURE

    def test_floors_are_exclusive_bounds(self) -> None:
        # exactly at floor = OK (floor is "below", not "at or below")
        assert (
            evaluate_result(POLICY, _task(), _subtask(), _result(confidence=0.35), _review(0.40))
            is None
        )


class TestPlanTriggers:
    def test_no_flags_no_escalation(self) -> None:
        assert evaluate_plan(POLICY, _task(), _plan()) is None

    def test_user_explicit_request(self) -> None:
        esc = evaluate_plan(POLICY, _task(require_review=True), _plan())
        assert esc is not None
        assert esc.reason is EscalationReason.EXPLICIT_REQUEST
        assert esc.level is ApprovalLevel.APPROVE_PLAN
        assert "research X" in esc.context  # plan visible to the human

    def test_policy_wide_plan_approval(self) -> None:
        policy = EscalationPolicy(require_plan_approval=True)
        esc = evaluate_plan(policy, _task(), _plan())
        assert esc is not None
        assert esc.level is ApprovalLevel.APPROVE_PLAN


class TestToolTrigger:
    def test_sensitive_tool_escalates_with_arguments_preview(self) -> None:
        esc = evaluate_tool(
            uuid4(),
            subtask_id="s1",
            tool_name="send_email",
            sensitive=True,
            arguments={"to": "ceo@corp.com", "body": "hello"},
        )
        assert esc is not None
        assert esc.reason is EscalationReason.SENSITIVE_TOOL
        assert esc.level is ApprovalLevel.APPROVE_ACTION
        assert "ceo@corp.com" in esc.context

    def test_normal_tool_passes(self) -> None:
        assert (
            evaluate_tool(
                uuid4(), subtask_id="s1", tool_name="read_file", sensitive=False, arguments={}
            )
            is None
        )
