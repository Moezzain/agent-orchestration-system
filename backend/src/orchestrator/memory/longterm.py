"""Long-term semantic memory in ChromaDB.

What survives task completion: what was asked, what approach worked, facts
discovered, user preferences observed. Future planning queries this to get
smarter over time.

Retrieval is importance-weighted: we over-fetch by similarity, then rerank by
combined score so a highly-important memory can beat a slightly-more-similar
trivial one.
"""

from collections.abc import Sequence
from datetime import UTC, datetime
from enum import StrEnum
from uuid import uuid4

import chromadb
from chromadb.api import AsyncClientAPI
from chromadb.api.models.AsyncCollection import AsyncCollection
from pydantic import BaseModel, ConfigDict, Field

from orchestrator.config import Settings
from orchestrator.memory.embedder import Embedder

_COLLECTION = "agent_memory"
_SIMILARITY_WEIGHT = 0.7
_IMPORTANCE_WEIGHT = 0.3
_OVERFETCH_FACTOR = 4


async def create_chroma_client(settings: Settings) -> AsyncClientAPI:
    return await chromadb.AsyncHttpClient(
        host=settings.chroma_host,
        port=settings.chroma_port,
        settings=chromadb.config.Settings(anonymized_telemetry=False),
    )


class MemoryKind(StrEnum):
    TASK_SUMMARY = "task_summary"  # what was asked + what approach worked
    FACT = "fact"  # domain knowledge discovered during a task
    PREFERENCE = "preference"  # observed user preference


class MemoryEntry(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: str = Field(default_factory=lambda: uuid4().hex)
    content: str = Field(min_length=1)
    kind: MemoryKind
    importance: float = Field(ge=0.0, le=1.0)
    task_id: str = ""  # chroma metadata forbids None; empty = no source task
    created_at: str = Field(default_factory=lambda: datetime.now(UTC).isoformat())


class ScoredMemory(BaseModel):
    model_config = ConfigDict(frozen=True)

    entry: MemoryEntry
    similarity: float
    score: float  # similarity blended with importance


class LongTermMemory:
    def __init__(
        self,
        client: AsyncClientAPI,
        embedder: Embedder,
        *,
        collection_name: str = _COLLECTION,
    ) -> None:
        self._client = client
        self._embedder = embedder
        self._collection_name = collection_name

    async def _collection(self) -> AsyncCollection:
        return await self._client.get_or_create_collection(
            self._collection_name,
            metadata={"hnsw:space": "cosine"},
        )

    async def add(self, entry: MemoryEntry) -> None:
        [embedding] = await self._embedder.embed([entry.content])
        # list is invariant: chroma's signature wants list[Sequence[...]]
        embeddings: list[Sequence[float] | Sequence[int]] = [embedding]
        collection = await self._collection()
        await collection.add(
            ids=[entry.id],
            embeddings=embeddings,
            documents=[entry.content],
            metadatas=[
                {
                    "kind": entry.kind.value,
                    "importance": entry.importance,
                    "task_id": entry.task_id,
                    "created_at": entry.created_at,
                }
            ],
        )

    async def search(
        self,
        query: str,
        *,
        limit: int = 5,
        kind: MemoryKind | None = None,
    ) -> list[ScoredMemory]:
        """Over-fetch by similarity, rerank by 0.7*similarity + 0.3*importance."""
        [query_embedding] = await self._embedder.embed([query])
        query_embeddings: list[Sequence[float] | Sequence[int]] = [query_embedding]
        collection = await self._collection()
        results = await collection.query(
            query_embeddings=query_embeddings,
            n_results=min(limit * _OVERFETCH_FACTOR, 50),
            where={"kind": kind.value} if kind else None,
            include=["documents", "metadatas", "distances"],
        )

        scored: list[ScoredMemory] = []
        ids = results["ids"][0]
        documents = (results["documents"] or [[]])[0]
        metadatas = (results["metadatas"] or [[]])[0]
        distances = (results["distances"] or [[]])[0]
        for entry_id, document, meta, distance in zip(
            ids, documents, metadatas, distances, strict=True
        ):
            similarity = 1.0 - distance  # cosine distance -> similarity
            importance = float(str(meta.get("importance", 0.0)))
            entry = MemoryEntry(
                id=entry_id,
                content=document,
                kind=MemoryKind(str(meta["kind"])),
                importance=importance,
                task_id=str(meta.get("task_id", "")),
                created_at=str(meta.get("created_at", "")),
            )
            scored.append(
                ScoredMemory(
                    entry=entry,
                    similarity=similarity,
                    score=_SIMILARITY_WEIGHT * similarity + _IMPORTANCE_WEIGHT * importance,
                )
            )

        scored.sort(key=lambda s: s.score, reverse=True)
        return scored[:limit]

    async def delete(self, entry_id: str) -> None:
        collection = await self._collection()
        await collection.delete(ids=[entry_id])

    async def delete_for_task(self, task_id: str) -> None:
        """User-data deletion plumbing: wipe everything a task contributed."""
        collection = await self._collection()
        await collection.delete(where={"task_id": task_id})

    async def count(self) -> int:
        collection = await self._collection()
        return int(await collection.count())
