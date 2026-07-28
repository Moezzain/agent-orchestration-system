"""OpenAI adapter: normalizes the OpenAI SDK to our provider contract.

The contrast with the Anthropic adapter is the point — same contract, three
wire-format differences handled here and nowhere else:
- system prompt is a message with role "system" (Anthropic: top-level param)
- tool args arrive as a JSON *string* (Anthropic: already-parsed dict)
- usage fields are prompt_tokens/completion_tokens (Anthropic: input/output)
"""

import json
import time
from collections.abc import Sequence

from openai import AsyncOpenAI, omit
from openai.types.chat import (
    ChatCompletion,
    ChatCompletionMessageParam,
    ChatCompletionToolParam,
)

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

_FINISH_REASON_MAP: dict[str | None, StopReason] = {
    "stop": "end_turn",
    "tool_calls": "tool_use",
    "length": "max_tokens",
    "content_filter": "refusal",
}


def _to_message_params(
    messages: Sequence[ConversationMessage], system: str | None
) -> list[ChatCompletionMessageParam]:
    params: list[ChatCompletionMessageParam] = []
    if system is not None:
        params.append({"role": "system", "content": system})
    for m in messages:
        if isinstance(m, ChatMessage):
            # branch narrows m.role to a literal, which the TypedDict union needs
            if m.role == "user":
                params.append({"role": "user", "content": m.content})
            else:
                params.append({"role": "assistant", "content": m.content})
        elif isinstance(m, AssistantToolCallMessage):
            # OpenAI: tool calls ride a dedicated field; arguments re-serialized to JSON
            params.append(
                {
                    "role": "assistant",
                    "content": m.text or None,
                    "tool_calls": [
                        {
                            "id": tc.id,
                            "type": "function",
                            "function": {
                                "name": tc.name,
                                "arguments": json.dumps(tc.arguments),
                            },
                        }
                        for tc in m.tool_calls
                    ],
                }
            )
        else:  # ToolResultMessage — one `tool`-role message PER result
            params.extend(
                {"role": "tool", "tool_call_id": r.tool_call_id, "content": r.output}
                for r in m.results
            )
    return params


def _to_tool_params(tools: Sequence[ToolSpec]) -> list[ChatCompletionToolParam]:
    return [
        {
            "type": "function",
            "function": {
                "name": t.name,
                "description": t.description,
                "parameters": dict(t.input_schema),
            },
        }
        for t in tools
    ]


def _parse_response(raw: ChatCompletion, *, latency_ms: int) -> LLMResponse:
    choice = raw.choices[0]
    message = choice.message

    tool_calls: list[ToolCall] = []
    for tc in message.tool_calls or []:
        if tc.type != "function":
            continue
        try:
            arguments = json.loads(tc.function.arguments)
        except json.JSONDecodeError as exc:
            raise ValueError(
                f"OpenAI returned malformed JSON for tool {tc.function.name!r}: "
                f"{tc.function.arguments!r}"
            ) from exc
        tool_calls.append(ToolCall(id=tc.id, name=tc.function.name, arguments=arguments))

    usage = raw.usage
    return LLMResponse(
        text=message.content or "",
        tool_calls=tool_calls,
        usage=Usage(
            input_tokens=usage.prompt_tokens if usage else 0,
            output_tokens=usage.completion_tokens if usage else 0,
        ),
        model=raw.model,
        provider="openai",
        stop_reason=_FINISH_REASON_MAP.get(choice.finish_reason, "other"),
        latency_ms=latency_ms,
    )


class OpenAIProvider:
    name = "openai"

    def __init__(self, api_key: str, *, max_retries: int = 3) -> None:
        self._client = AsyncOpenAI(api_key=api_key, max_retries=max_retries)

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
        raw = await self._client.chat.completions.create(
            model=model,
            max_completion_tokens=max_tokens,
            messages=_to_message_params(messages, system),
            tools=_to_tool_params(tools) if tools else omit,
        )
        latency_ms = int((time.perf_counter() - start) * 1000)
        return _parse_response(raw, latency_ms=latency_ms)
