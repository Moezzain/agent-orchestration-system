"""The Reviewer: judges a specialist's output against its subtask contract.

Verdict is a routing instruction for the graph:
    accept   -> result stands
    revise   -> back to the specialist WITH the feedback (one loop max,
                enforced by the graph, not here)
    escalate -> a human needs to look at this

The LLM never echoes identifiers: it produces a judgment; we attach it to the
subtask ourselves.
"""

from pydantic import BaseModel, ConfigDict, Field

from orchestrator.llm.provider import LLMProvider
from orchestrator.llm.router import ModelRouter
from orchestrator.llm.structured import complete_structured
from orchestrator.llm.types import ChatMessage
from orchestrator.schemas import AgentResult, AgentRole, ReviewResult, ReviewVerdict, SubTask

_OUTPUT_EXCERPT_CHARS = 8_000

_SYSTEM = """You are the quality reviewer of a multi-agent team. Judge whether a
specialist's output fulfills its subtask contract.

Rubric — weigh each:
- Contract fit: does the output match expected_output in substance AND format?
- Completeness: is every part of the subtask description addressed?
- Groundedness: are claims supported (citations for research, executed code
  for numbers)? Penalize confident assertions without support.
- Usability: could a teammate consume this directly, without rework?

Verdict rules:
- accept: fit for purpose, minor imperfections allowed.
- revise: fixable by the same specialist — give concrete, actionable feedback
  naming exactly what to change. Never vague ("improve quality").
- escalate: fundamentally off-track, the subtask looks unachievable as
  specified, or the output makes claims you cannot trust — a human must look.

Score honestly on [0,1]; do not cluster around 0.7. An empty or error output
scores near 0."""


class _Judgment(BaseModel):
    """What the LLM produces — no ids to echo (we attach those)."""

    model_config = ConfigDict(frozen=True)

    score: float = Field(ge=0.0, le=1.0, description="Overall quality per the rubric.")
    verdict: ReviewVerdict
    issues: list[str] = Field(
        default_factory=list, description="Specific problems found; empty when accepting."
    )
    feedback: str = Field(
        default="",
        description="For revise: exact instructions for the fix. Otherwise brief rationale.",
    )


class Reviewer:
    def __init__(self, provider: LLMProvider, model: str) -> None:
        self._provider = provider
        self._model = model

    async def review(self, subtask: SubTask, result: AgentResult) -> ReviewResult:
        prompt = self._build_prompt(subtask, result)
        judgment = await complete_structured(
            self._provider,
            model=self._model,
            messages=[ChatMessage(role="user", content=prompt)],
            response_model=_Judgment,
            system=_SYSTEM,
        )
        return ReviewResult(
            subtask_id=subtask.id,
            score=judgment.score,
            verdict=judgment.verdict,
            issues=judgment.issues,
            feedback=judgment.feedback,
        )

    def _build_prompt(self, subtask: SubTask, result: AgentResult) -> str:
        output = result.output[:_OUTPUT_EXCERPT_CHARS]
        truncated = " [truncated]" if len(result.output) > _OUTPUT_EXCERPT_CHARS else ""
        sections = [
            f"# Subtask contract\ndescription: {subtask.description}\n"
            f"expected_output: {subtask.expected_output}\n"
            f"specialist: {subtask.specialist.value}",
            f"# Specialist output (self-reported confidence {result.confidence:.2f})"
            f"\n{output}{truncated}",
        ]
        if result.error is not None:
            sections.append(f"# Execution flag\n{result.error}")
        return "\n\n".join(sections)


def build_reviewer(router: ModelRouter) -> Reviewer:
    route = router.resolve(AgentRole.REVIEWER)
    return Reviewer(route.provider, route.model)
