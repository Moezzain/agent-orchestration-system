"""The Supervisor: task -> typed Plan, informed by long-term memory.

Decomposition rides complete_structured with Plan as the response model, so
the Plan's own validators (unique ids, no dangling deps, no cycles) become
the retry loop's acceptance criteria: an unexecutable plan never leaves this
module.
"""

from typing import Protocol

from pydantic import BaseModel, ConfigDict

from orchestrator.llm.provider import LLMProvider
from orchestrator.llm.router import ModelRouter
from orchestrator.llm.structured import complete_structured
from orchestrator.llm.types import ChatMessage
from orchestrator.memory.longterm import MemoryKind, ScoredMemory
from orchestrator.schemas import AgentRole, Plan, Task

_MEMORY_LIMIT = 5

_SYSTEM = """You are the supervisor of a multi-agent team. Decompose the user's
task into an executable plan of subtasks.

Your specialists:
- research: searches the web, gathers and cites current information, saves
  findings to the shared workspace.
- writer: turns research/analysis into polished deliverables in the requested
  format; reads teammates' workspace files.
- data_analysis: computes with sandboxed Python; every number comes from code.
- code_execution: writes and runs Python, iterating on errors.

Planning rules:
- 2 to 6 subtasks. Each must be self-contained: a specialist sees ONLY its
  description, expected_output, and its dependencies' outputs.
- Use depends_on ONLY when a subtask genuinely needs another's output.
  Independent subtasks run in parallel — don't chain them needlessly.
- Tell producing subtasks WHERE to save workspace files and consuming
  subtasks WHICH files to read.
- If relevant memories from past tasks are provided, apply user preferences
  and reuse approaches that worked; avoid approaches that failed."""


class MemorySearch(Protocol):
    """The one slice of LongTermMemory the supervisor needs (narrow seam)."""

    async def search(
        self, query: str, *, limit: int = 5, kind: MemoryKind | None = None
    ) -> list[ScoredMemory]: ...


class PlanningOutcome(BaseModel):
    """Plan + the memories that informed it — observability for 'memory
    influenced this decision' in the trace/UI."""

    model_config = ConfigDict(frozen=True)

    plan: Plan
    memories_used: list[ScoredMemory]


def _format_memories(memories: list[ScoredMemory]) -> str:
    lines = [
        f"- [{m.entry.kind.value}, importance {m.entry.importance:.1f}] {m.entry.content}"
        for m in memories
    ]
    return "# Relevant memories from past tasks\n" + "\n".join(lines)


class Supervisor:
    def __init__(
        self,
        provider: LLMProvider,
        model: str,
        *,
        memory: MemorySearch | None = None,
    ) -> None:
        self._provider = provider
        self._model = model
        self._memory = memory

    async def plan(self, task: Task) -> PlanningOutcome:
        memories: list[ScoredMemory] = []
        if self._memory is not None:
            memories = await self._memory.search(task.request, limit=_MEMORY_LIMIT)

        sections = [f"# Task\n{task.request}"]
        if memories:
            sections.append(_format_memories(memories))

        plan = await complete_structured(
            self._provider,
            model=self._model,
            messages=[ChatMessage(role="user", content="\n\n".join(sections))],
            response_model=Plan,
            system=_SYSTEM,
        )
        return PlanningOutcome(plan=plan, memories_used=memories)


def build_supervisor(router: ModelRouter, *, memory: MemorySearch | None = None) -> Supervisor:
    route = router.resolve(AgentRole.SUPERVISOR)
    return Supervisor(route.provider, route.model, memory=memory)
