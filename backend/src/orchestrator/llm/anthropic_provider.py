"""Anthropic adapter: normalizes the Anthropic SDK to our provider contract.

Retry note: the official SDK already retries connection errors, 408/409/429,
and 5xx with exponential backoff (max_retries) — we configure it instead of
wrapping calls in our own retry layer.
"""

import time
from collections.abc import Sequence

from anthropic import AsyncAnthropic, omit
from anthropic.types import Message as AnthropicMessage
from anthropic.types import MessageParam, ToolParam

from orchestrator.llm.types import (
    AssistantToolCallMessage,
    ChatMessage,
    ConversationMessage,
    LLMResponse,
    StopReason,
    ToolCall,
    ToolSpec,
    Usage,
)

_STOP_REASON_MAP: dict[str | None, StopReason] = {
    "end_turn": "end_turn",
    "tool_use": "tool_use",
    "max_tokens": "max_tokens",
    "refusal": "refusal",
}


def _to_message_params(messages: Sequence[ConversationMessage]) -> list[MessageParam]:
    params: list[MessageParam] = []
    for m in messages:
        if isinstance(m, ChatMessage):
            params.append({"role": m.role, "content": m.content})
        elif isinstance(m, AssistantToolCallMessage):
            # assistant turn = content blocks: optional text, then tool_use blocks
            blocks: list[dict[str, object]] = []
            if m.text:
                blocks.append({"type": "text", "text": m.text})
            blocks.extend(
                {"type": "tool_use", "id": tc.id, "name": tc.name, "input": tc.arguments}
                for tc in m.tool_calls
            )
            params.append({"role": "assistant", "content": blocks})  # type: ignore[typeddict-item]
        else:  # ToolResultMessage — Anthropic wants results as USER content blocks
            result_blocks: list[dict[str, object]] = [
                {
                    "type": "tool_result",
                    "tool_use_id": r.tool_call_id,
                    "content": r.output,
                    "is_error": r.is_error,
                }
                for r in m.results
            ]
            params.append({"role": "user", "content": result_blocks})  # type: ignore[typeddict-item]
    return params


def _to_tool_params(tools: Sequence[ToolSpec]) -> list[ToolParam]:
    return [
        {
            "name": t.name,
            "description": t.description,
            "input_schema": dict(t.input_schema),
        }
        for t in tools
    ]


def _parse_response(raw: AnthropicMessage, *, latency_ms: int) -> LLMResponse:
    text_parts: list[str] = []
    tool_calls: list[ToolCall] = []
    for block in raw.content:
        if block.type == "text":
            text_parts.append(block.text)
        elif block.type == "tool_use":
            tool_calls.append(ToolCall(id=block.id, name=block.name, arguments=dict(block.input)))
    return LLMResponse(
        text="".join(text_parts),
        tool_calls=tool_calls,
        usage=Usage(
            input_tokens=raw.usage.input_tokens,
            output_tokens=raw.usage.output_tokens,
        ),
        model=raw.model,
        provider="anthropic",
        stop_reason=_STOP_REASON_MAP.get(raw.stop_reason, "other"),
        latency_ms=latency_ms,
    )


class AnthropicProvider:
    name = "anthropic"

    def __init__(self, api_key: str, *, max_retries: int = 3) -> None:
        self._client = AsyncAnthropic(api_key=api_key, max_retries=max_retries)

    async def complete(
        self,
        *,
        model: str,
        messages: Sequence[ConversationMessage],
        system: str | None = None,
        tools: Sequence[ToolSpec] | None = None,
        max_tokens: int = 4096,
    ) -> LLMResponse:
        start = time.perf_counter()
        raw = await self._client.messages.create(
            model=model,
            max_tokens=max_tokens,
            messages=_to_message_params(messages),
            # Anthropic wants system as a top-level param, not a message role;
            # `omit` is the SDK's "field not sent" sentinel
            system=system if system is not None else omit,
            tools=_to_tool_params(tools) if tools else omit,
        )
        latency_ms = int((time.perf_counter() - start) * 1000)
        return _parse_response(raw, latency_ms=latency_ms)
