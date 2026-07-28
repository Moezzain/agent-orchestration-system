"""Memory consolidation: distill a finished task's working memory into
long-term entries. This is the write-side of "the system gets smarter" —
runs once, at task completion, before working memory is cleared.
"""

from pydantic import BaseModel, ConfigDict, Field

from orchestrator.llm.provider import LLMProvider
from orchestrator.llm.structured import complete_structured
from orchestrator.llm.types import ChatMessage
from orchestrator.memory.longterm import LongTermMemory, MemoryEntry, MemoryKind
from orchestrator.memory.working import WorkingMemory
from orchestrator.schemas import Task

_RESULT_EXCERPT_CHARS = 1_200

_SYSTEM = """You extract reusable memories from a completed multi-agent task run.
Good memories help plan FUTURE tasks: approaches that worked, approaches that
failed and why, stable user preferences, and durable domain facts.
Do NOT store one-off details with no future value. Fewer, better memories win."""


class ConsolidatedMemory(BaseModel):
    model_config = ConfigDict(frozen=True)

    content: str = Field(
        min_length=10,
        description="The memory, self-contained and understandable without this task's context.",
    )
    kind: MemoryKind = Field(
        description="task_summary = what was asked + approach + outcome; "
        "fact = durable domain knowledge; preference = observed user preference."
    )
    importance: float = Field(
        ge=0.0,
        le=1.0,
        description="Future usefulness: reusable approaches and stable preferences high "
        "(0.7+); narrow one-off facts low (0.3 or less).",
    )


class ConsolidationOutput(BaseModel):
    model_config = ConfigDict(frozen=True)

    memories: list[ConsolidatedMemory] = Field(
        max_length=5,
        description="0-5 memories worth keeping. An empty list is a valid answer.",
    )


class MemoryConsolidator:
    def __init__(self, provider: LLMProvider, model: str, long_term: LongTermMemory) -> None:
        self._provider = provider
        self._model = model
        self._long_term = long_term

    async def consolidate(self, task: Task, working: WorkingMemory) -> list[MemoryEntry]:
        prompt = await self._build_prompt(task, working)
        output = await complete_structured(
            self._provider,
            model=self._model,
            messages=[ChatMessage(role="user", content=prompt)],
            response_model=ConsolidationOutput,
            system=_SYSTEM,
        )

        entries = [
            MemoryEntry(
                content=m.content,
                kind=m.kind,
                importance=m.importance,
                task_id=str(task.id),
            )
            for m in output.memories
        ]
        for entry in entries:
            await self._long_term.add(entry)
        return entries

    async def _build_prompt(self, task: Task, working: WorkingMemory) -> str:
        plan = await working.load_plan()
        results = await working.load_results()
        errors = await working.errors()

        sections = [f"# Original request\n{task.request}"]
        if plan is not None:
            sections.append(f"# Plan reasoning\n{plan.reasoning}")
            steps = "\n".join(
                f"- {s.id} [{s.specialist.value}] {s.description}" for s in plan.subtasks
            )
            sections.append(f"# Subtasks\n{steps}")
        if results:
            blocks = [
                f"## {sid} (confidence {r.confidence:.2f})\n{r.output[:_RESULT_EXCERPT_CHARS]}"
                for sid, r in sorted(results.items())
            ]
            sections.append("# Subtask results\n" + "\n".join(blocks))
        if errors:
            sections.append("# Errors during execution\n" + "\n".join(f"- {e}" for e in errors))

        return "\n\n".join(sections)
