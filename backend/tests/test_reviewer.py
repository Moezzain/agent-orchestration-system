import json

from orchestrator.agents import Reviewer
from orchestrator.llm.types import ChatMessage
from orchestrator.schemas import AgentResult, ReviewVerdict, SpecialistType, SubTask
from tests.fakes import ScriptedProvider


def _subtask() -> SubTask:
    return SubTask(
        id="s1",
        description="research chroma vs pgvector",
        specialist=SpecialistType.RESEARCH,
        expected_output="cited markdown notes",
        complexity=2,
    )


def _result(output: str, confidence: float = 0.8, error: str | None = None) -> AgentResult:
    return AgentResult(
        subtask_id="s1",
        specialist=SpecialistType.RESEARCH,
        output=output,
        confidence=confidence,
        error=error,
    )


def _judgment(
    verdict: str, score: float, feedback: str = "", issues: list[str] | None = None
) -> str:
    return json.dumps(
        {"score": score, "verdict": verdict, "issues": issues or [], "feedback": feedback}
    )


async def test_accept_maps_to_review_result_with_our_subtask_id() -> None:
    provider = ScriptedProvider([_judgment("accept", 0.9, "solid, well cited")])
    reviewer = Reviewer(provider, "review-model")

    review = await reviewer.review(_subtask(), _result("good notes [1]..."))

    assert review.verdict is ReviewVerdict.ACCEPT
    assert review.score == 0.9
    assert review.subtask_id == "s1"  # attached by us, never echoed by the LLM


async def test_revise_carries_actionable_feedback() -> None:
    provider = ScriptedProvider(
        [_judgment("revise", 0.45, "add sources for the latency claims", ["uncited claims"])]
    )
    reviewer = Reviewer(provider, "review-model")

    review = await reviewer.review(_subtask(), _result("notes without citations"))

    assert review.verdict is ReviewVerdict.REVISE
    assert review.issues == ["uncited claims"]
    assert "add sources" in review.feedback


async def test_prompt_carries_contract_output_and_error_flag() -> None:
    provider = ScriptedProvider([_judgment("escalate", 0.1)])
    reviewer = Reviewer(provider, "review-model")

    await reviewer.review(
        _subtask(), _result("partial", confidence=0.2, error="turn budget exhausted after 8 turns")
    )

    first = provider.calls[0][0]
    assert isinstance(first, ChatMessage)
    assert "cited markdown notes" in first.content  # expected_output
    assert "confidence 0.20" in first.content
    assert "turn budget exhausted" in first.content  # error surfaced to reviewer


async def test_oversized_output_truncated_in_prompt() -> None:
    provider = ScriptedProvider([_judgment("accept", 0.8)])
    reviewer = Reviewer(provider, "review-model")

    await reviewer.review(_subtask(), _result("x" * 20_000))

    first = provider.calls[0][0]
    assert isinstance(first, ChatMessage)
    assert "[truncated]" in first.content
    assert len(first.content) < 12_000
