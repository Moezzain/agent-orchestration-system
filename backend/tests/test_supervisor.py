import json

from orchestrator.agents import Supervisor
from orchestrator.llm.types import ChatMessage
from orchestrator.memory.longterm import MemoryEntry, MemoryKind, ScoredMemory
from orchestrator.schemas import Task
from tests.fakes import ScriptedProvider


def _plan_json(subtasks: list[dict[str, object]]) -> str:
    return json.dumps({"reasoning": "split the work", "subtasks": subtasks})


VALID_SUBTASKS: list[dict[str, object]] = [
    {
        "id": "s1",
        "description": "research X, save to research/x.md",
        "specialist": "research",
        "depends_on": [],
        "expected_output": "cited notes",
        "complexity": 2,
    },
    {
        "id": "s2",
        "description": "write summary from research/x.md",
        "specialist": "writer",
        "depends_on": ["s1"],
        "expected_output": "markdown summary",
        "complexity": 2,
    },
]


class FakeMemory:
    def __init__(self, results: list[ScoredMemory]) -> None:
        self._results = results
        self.queries: list[str] = []

    async def search(
        self, query: str, *, limit: int = 5, kind: MemoryKind | None = None
    ) -> list[ScoredMemory]:
        self.queries.append(query)
        return self._results


def _memory(content: str, kind: MemoryKind = MemoryKind.PREFERENCE) -> ScoredMemory:
    return ScoredMemory(
        entry=MemoryEntry(content=content, kind=kind, importance=0.9),
        similarity=0.8,
        score=0.83,
    )


async def test_valid_plan_parsed_and_typed() -> None:
    provider = ScriptedProvider([_plan_json(VALID_SUBTASKS)])
    supervisor = Supervisor(provider, "strong-model")

    outcome = await supervisor.plan(Task(request="research X and summarize"))

    assert len(outcome.plan.subtasks) == 2
    assert outcome.plan.subtasks[1].depends_on == ["s1"]
    assert outcome.plan.execution_batches()[0][0].id == "s1"
    assert outcome.memories_used == []


async def test_unexecutable_plan_rejected_and_retried() -> None:
    # first attempt has a dependency cycle — Plan's own validator rejects it,
    # the retry loop feeds the error back, second attempt is valid
    cyclic = [
        {**VALID_SUBTASKS[0], "depends_on": ["s2"]},
        VALID_SUBTASKS[1],
    ]
    provider = ScriptedProvider([_plan_json(cyclic), _plan_json(VALID_SUBTASKS)])
    supervisor = Supervisor(provider, "strong-model")

    outcome = await supervisor.plan(Task(request="task"))

    assert len(provider.calls) == 2
    retry_msg = provider.calls[1][-1]
    assert isinstance(retry_msg, ChatMessage)
    assert "cycle" in retry_msg.content
    assert outcome.plan.subtasks[0].depends_on == []


async def test_memories_retrieved_and_injected_into_prompt() -> None:
    memory = FakeMemory([_memory("User prefers markdown tables.")])
    provider = ScriptedProvider([_plan_json(VALID_SUBTASKS)])
    supervisor = Supervisor(provider, "strong-model", memory=memory)

    outcome = await supervisor.plan(Task(request="compare A and B"))

    assert memory.queries == ["compare A and B"]
    first = provider.calls[0][0]
    assert isinstance(first, ChatMessage)
    assert "Relevant memories" in first.content
    assert "User prefers markdown tables." in first.content
    assert len(outcome.memories_used) == 1


async def test_no_memory_store_no_memory_section() -> None:
    provider = ScriptedProvider([_plan_json(VALID_SUBTASKS)])
    supervisor = Supervisor(provider, "strong-model")

    await supervisor.plan(Task(request="task"))

    first = provider.calls[0][0]
    assert isinstance(first, ChatMessage)
    assert "Relevant memories" not in first.content
