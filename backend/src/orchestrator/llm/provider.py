"""The provider contract every LLM adapter implements."""

from collections.abc import Sequence
from typing import Protocol

from orchestrator.llm.types import ConversationMessage, LLMResponse, ToolSpec


class LLMProvider(Protocol):
    """Structural interface — adapters satisfy it by shape, no inheritance needed."""

    name: str

    async def complete(
        self,
        *,
        model: str,
        messages: Sequence[ConversationMessage],
        system: str | None = None,
        tools: Sequence[ToolSpec] | None = None,
        max_tokens: int = 4096,
    ) -> LLMResponse: ...
