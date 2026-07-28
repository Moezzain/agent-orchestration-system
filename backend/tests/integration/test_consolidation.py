"""Consolidation tests — need live Redis + Chroma. LLM is scripted (no network)."""

import json
from collections.abc import AsyncIterator, Sequence
from uuid import uuid4

import pytest

from orchestrator.config import Settings
from orchestrator.llm.types import ChatMessage
from orchestrator.memory import (
    LongTermMemory,
    MemoryKind,
    WorkingMemory,
    create_chroma_client,
    create_working_memory_client,
)
from orchestrator.memory.lifecycle import MemoryConsolidator
from orchestrator.schemas import AgentResult, SpecialistType, Task
from tests.fakes import ScriptedProvider


class CountingEmbedder:
    """Deterministic tiny vectors — content-independent, fine for storage tests."""

    async def embed(self, texts: Sequence[str]) -> list[list[float]]:
        return [[1.0, 0.0] for _ in texts]


@pytest.fixture
async def stores() -> AsyncIterator[tuple[WorkingMemory, LongTermMemory, Task]]:
    settings = Settings(_env_file=None)  # type: ignore[call-arg]
    try:
        chroma = await create_chroma_client(settings)
        await chroma.heartbeat()
        redis_client = create_working_memory_client(settings)
        await redis_client.ping()
    except Exception:
        pytest.skip("redis/chroma not reachable — run `make infra-up`")

    task = Task(request="research chroma vs pgvector and summarize")
    working = WorkingMemory(redis_client, task.id)
    collection = f"test_{uuid4().hex}"
    long_term = LongTermMemory(chroma, CountingEmbedder(), collection_name=collection)

    yield working, long_term, task

    await working.clear()
    await chroma.delete_collection(collection)
    await redis_client.aclose()


def _llm_output() -> str:
    return json.dumps(
        {
            "memories": [
                {
                    "content": "For DB comparison tasks, research-then-write with two "
                    "specialists worked well.",
                    "kind": "task_summary",
                    "importance": 0.8,
                },
                {
                    "content": "User prefers comparisons as markdown tables.",
                    "kind": "preference",
                    "importance": 0.9,
                },
            ]
        }
    )


async def test_consolidation_distills_working_memory_to_longterm(
    stores: tuple[WorkingMemory, LongTermMemory, Task],
) -> None:
    working, long_term, task = stores
    await working.save_result(
        AgentResult(
            subtask_id="s1",
            specialist=SpecialistType.RESEARCH,
            output="chroma is a dedicated vector db; pgvector is a postgres extension",
            confidence=0.85,
        )
    )
    provider = ScriptedProvider([_llm_output()])
    consolidator = MemoryConsolidator(provider, "test-model", long_term)

    entries = await consolidator.consolidate(task, working)

    assert len(entries) == 2
    assert await long_term.count() == 2
    # entries carry the source task for delete_for_task plumbing
    assert all(e.task_id == str(task.id) for e in entries)
    # prompt actually contained the working memory content
    first_message = provider.calls[0][0]
    assert isinstance(first_message, ChatMessage)
    prompt = first_message.content
    assert "chroma is a dedicated vector db" in prompt
    assert task.request in prompt


async def test_empty_memories_is_valid_outcome(
    stores: tuple[WorkingMemory, LongTermMemory, Task],
) -> None:
    working, long_term, task = stores
    provider = ScriptedProvider(['{"memories": []}'])
    consolidator = MemoryConsolidator(provider, "test-model", long_term)

    entries = await consolidator.consolidate(task, working)

    assert entries == []
    assert await long_term.count() == 0


async def test_preference_kind_survives_roundtrip(
    stores: tuple[WorkingMemory, LongTermMemory, Task],
) -> None:
    working, long_term, task = stores
    provider = ScriptedProvider([_llm_output()])
    consolidator = MemoryConsolidator(provider, "test-model", long_term)

    await consolidator.consolidate(task, working)

    prefs = await long_term.search("formatting", limit=5, kind=MemoryKind.PREFERENCE)
    assert len(prefs) == 1
    assert "markdown tables" in prefs[0].entry.content
