"""Long-term memory tests — need live Chroma (make infra-up). No network
embeddings: a deterministic fake embedder controls similarity exactly."""

from collections.abc import AsyncIterator, Sequence
from uuid import uuid4

import pytest

from orchestrator.config import Settings
from orchestrator.memory import LongTermMemory, MemoryEntry, MemoryKind, create_chroma_client


class FakeEmbedder:
    """Maps known texts to fixed vectors — similarity becomes arithmetic."""

    def __init__(self, vectors: dict[str, list[float]]) -> None:
        self._vectors = vectors

    async def embed(self, texts: Sequence[str]) -> list[list[float]]:
        return [self._vectors[t] for t in texts]


VECTORS = {
    "postgres index tuning lessons": [1.0, 0.0, 0.0],
    "user prefers markdown tables": [0.0, 1.0, 0.0],
    "chroma collection basics": [0.0, 0.0, 1.0],
    # query vectors
    "database performance": [0.95, 0.05, 0.05],
    "how should I format output": [0.05, 0.95, 0.0],
}


@pytest.fixture
async def memory() -> AsyncIterator[LongTermMemory]:
    settings = Settings(_env_file=None)  # type: ignore[call-arg]
    try:
        client = await create_chroma_client(settings)
        await client.heartbeat()
    except Exception:
        pytest.skip("chroma not reachable — run `make infra-up`")

    collection_name = f"test_{uuid4().hex}"
    ltm = LongTermMemory(client, FakeEmbedder(VECTORS), collection_name=collection_name)
    yield ltm
    await client.delete_collection(collection_name)


def _entry(
    content: str, kind: MemoryKind = MemoryKind.FACT, importance: float = 0.5
) -> MemoryEntry:
    return MemoryEntry(content=content, kind=kind, importance=importance, task_id="task-1")


async def test_search_returns_semantically_nearest(memory: LongTermMemory) -> None:
    await memory.add(_entry("postgres index tuning lessons"))
    await memory.add(_entry("user prefers markdown tables", kind=MemoryKind.PREFERENCE))
    await memory.add(_entry("chroma collection basics"))

    results = await memory.search("database performance", limit=1)

    assert results[0].entry.content == "postgres index tuning lessons"
    assert results[0].similarity > 0.9


async def test_importance_breaks_near_ties(memory: LongTermMemory) -> None:
    # two memories, nearly equidistant from the query; importance decides
    vectors = {
        "approach A notes": [0.7, 0.7, 0.0],
        "approach B notes": [0.7, 0.72, 0.0],
        "which approach": [0.7, 0.71, 0.0],
    }
    memory._embedder = FakeEmbedder(vectors)  # noqa: SLF001 — test reaches into private

    await memory.add(_entry("approach A notes", importance=0.9))
    await memory.add(_entry("approach B notes", importance=0.1))

    results = await memory.search("which approach", limit=2)

    assert results[0].entry.content == "approach A notes"  # importance won
    assert results[0].score > results[1].score


async def test_kind_filter(memory: LongTermMemory) -> None:
    await memory.add(_entry("postgres index tuning lessons"))
    await memory.add(_entry("user prefers markdown tables", kind=MemoryKind.PREFERENCE))

    results = await memory.search("database performance", limit=5, kind=MemoryKind.PREFERENCE)

    assert len(results) == 1
    assert results[0].entry.kind is MemoryKind.PREFERENCE


async def test_delete_for_task_wipes_contributions(memory: LongTermMemory) -> None:
    await memory.add(_entry("postgres index tuning lessons"))
    await memory.add(_entry("user prefers markdown tables", kind=MemoryKind.PREFERENCE))
    assert await memory.count() == 2

    await memory.delete_for_task("task-1")

    assert await memory.count() == 0
