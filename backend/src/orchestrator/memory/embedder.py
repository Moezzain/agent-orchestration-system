"""Embedding contract + OpenAI implementation.

Embeddings are OUR concern, not Chroma's: passing vectors in keeps Chroma a
pure vector store (no client-side model downloads, no hidden embedding
config) and makes the embedder swappable/fakeable — tests inject a
deterministic one.
"""

from collections.abc import Sequence
from typing import Protocol

from openai import AsyncOpenAI


class Embedder(Protocol):
    async def embed(self, texts: Sequence[str]) -> list[list[float]]: ...


class OpenAIEmbedder:
    """text-embedding-3-small: cheap ($0.02/MTok), 1536 dims, plenty for
    memory retrieval. Anthropic has no embeddings API, so OpenAI serves both
    routes here."""

    def __init__(self, api_key: str, *, model: str = "text-embedding-3-small") -> None:
        self._client = AsyncOpenAI(api_key=api_key)
        self._model = model

    async def embed(self, texts: Sequence[str]) -> list[list[float]]:
        response = await self._client.embeddings.create(model=self._model, input=list(texts))
        # API may return out of order in theory; index field is authoritative
        ordered = sorted(response.data, key=lambda item: item.index)
        return [item.embedding for item in ordered]
