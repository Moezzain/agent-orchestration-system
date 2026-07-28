"""Shared test fakes."""

from collections.abc import Sequence

from orchestrator.llm.types import (
    ConversationMessage,
    LLMResponse,
    ToolCall,
    ToolSpec,
    Usage,
)


def text_response(text: str, *, stop_reason: str = "end_turn") -> LLMResponse:
    return LLMResponse(
        text=text,
        usage=Usage(input_tokens=10, output_tokens=10),
        model="fake-model",
        provider="scripted",
        stop_reason=stop_reason,  # type: ignore[arg-type]
        latency_ms=1,
    )


def tool_call_response(*calls: ToolCall, text: str = "") -> LLMResponse:
    return LLMResponse(
        text=text,
        tool_calls=list(calls),
        usage=Usage(input_tokens=10, output_tokens=10),
        model="fake-model",
        provider="scripted",
        stop_reason="tool_use",
        latency_ms=1,
    )


class ScriptedProvider:
    """LLMProvider fake returning queued responses in order. Records calls.

    Accepts plain strings (wrapped as end_turn text responses) or full
    LLMResponse objects (for tool-call turns).
    """

    name = "scripted"

    def __init__(self, responses: Sequence[str | LLMResponse]) -> None:
        self._queue: list[LLMResponse] = [
            text_response(r) if isinstance(r, str) else r for r in responses
        ]
        self.calls: list[list[ConversationMessage]] = []
        self.systems: list[str | None] = []

    async def complete(
        self,
        *,
        model: str,
        messages: Sequence[ConversationMessage],
        system: str | None = None,
        tools: Sequence[ToolSpec] | None = None,
        max_tokens: int = 4096,
    ) -> LLMResponse:
        self.calls.append(list(messages))
        self.systems.append(system)
        if not self._queue:
            raise AssertionError("ScriptedProvider ran out of responses")
        response = self._queue.pop(0)
        return response.model_copy(update={"model": model})
