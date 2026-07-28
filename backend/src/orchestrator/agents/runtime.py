"""The generic agent: system prompt + tool loop -> AgentResult.

Every specialist is this one runtime with different configuration (prompt,
tools, model). The loop:

    LLM -> wants tools? -> execute via registry -> feed results back -> repeat
        -> final text (or refusal / turn budget exhausted) -> AgentResult

Failures inside the loop are conversation content, not exceptions — the model
sees tool errors and adapts. Only infrastructure failures escape.
"""

import asyncio
import re
from collections.abc import Awaitable, Callable
from uuid import UUID

from opentelemetry import trace

from orchestrator.llm.provider import LLMProvider
from orchestrator.llm.types import (
    AssistantToolCallMessage,
    ChatMessage,
    ConversationMessage,
    LLMResponse,
    ToolResult,
    ToolResultMessage,
)
from orchestrator.observability.otel import KIND_ATTR, TASK_ID_ATTR
from orchestrator.schemas import AgentResult, SpecialistType
from orchestrator.tools.base import InvocationContext
from orchestrator.tools.registry import ToolRegistry

_CONFIDENCE_PATTERN = re.compile(r"^\s*CONFIDENCE:\s*([01](?:\.\d+)?)\s*$", re.MULTILINE)
_DEFAULT_CONFIDENCE = 0.5
_EXHAUSTED_CONFIDENCE = 0.2

_SHARED_SUFFIX = """

When you have completed the task, give your final answer as plain text.
End your final answer with a line of exactly this form:
CONFIDENCE: <0.0-1.0>
where the number is your honest assessment that your output meets the task's
expected-output requirements. Do not add anything after that line."""

# Called after every LLM response — wiring point for cost recording (step 24)
ResponseCallback = Callable[[LLMResponse], Awaitable[None]]


def _extract_confidence(text: str) -> tuple[str, float]:
    """Pull the trailing CONFIDENCE marker out of the answer; default if absent."""
    matches = list(_CONFIDENCE_PATTERN.finditer(text))
    if not matches:
        return text.strip(), _DEFAULT_CONFIDENCE
    last = matches[-1]
    confidence = min(1.0, max(0.0, float(last.group(1))))
    cleaned = (text[: last.start()] + text[last.end() :]).strip()
    return cleaned, confidence


class AgentRuntime:
    def __init__(
        self,
        *,
        provider: LLMProvider,
        model: str,
        specialist: SpecialistType,
        system_prompt: str,
        registry: ToolRegistry,
        tool_names: list[str],
        max_turns: int = 8,
        max_tokens: int = 4096,
        on_llm_response: ResponseCallback | None = None,
    ) -> None:
        self._provider = provider
        self._model = model
        self._specialist = specialist
        self._system = system_prompt + _SHARED_SUFFIX
        self._registry = registry
        self._tool_names = tool_names
        self._max_turns = max_turns
        self._max_tokens = max_tokens
        self._on_llm_response = on_llm_response

    async def run(
        self,
        subtask_description: str,
        *,
        subtask_id: str,
        context: str = "",
        task_id: UUID | None = None,
    ) -> AgentResult:
        prompt = subtask_description if not context else f"{subtask_description}\n\n{context}"
        conversation: list[ConversationMessage] = [ChatMessage(role="user", content=prompt)]
        specs = self._registry.specs(self._tool_names)

        tracer = trace.get_tracer("orchestrator.llm")
        last_text = ""
        for turn in range(self._max_turns):
            with tracer.start_as_current_span("llm.complete") as span:
                response = await self._provider.complete(
                    model=self._model,
                    messages=conversation,
                    system=self._system,
                    tools=specs or None,
                    max_tokens=self._max_tokens,
                )
                span.set_attribute(KIND_ATTR, "llm_call")
                if task_id is not None:
                    span.set_attribute(TASK_ID_ATTR, str(task_id))
                span.set_attribute("llm.model", response.model)
                span.set_attribute("llm.provider", response.provider)
                span.set_attribute("llm.input_tokens", response.usage.input_tokens)
                span.set_attribute("llm.output_tokens", response.usage.output_tokens)
                span.set_attribute("llm.stop_reason", response.stop_reason)
                span.set_attribute("llm.turn", turn)
                span.set_attribute("agent.specialist", self._specialist.value)
            if self._on_llm_response is not None:
                await self._on_llm_response(response)
            last_text = response.text

            if response.stop_reason == "refusal":
                return self._result(
                    subtask_id, output="", confidence=0.0, error="model refused the task"
                )

            if not response.tool_calls:
                output, confidence = _extract_confidence(response.text)
                return self._result(subtask_id, output=output, confidence=confidence)

            # echo the model's own requests, then execute them concurrently
            conversation.append(
                AssistantToolCallMessage(text=response.text, tool_calls=response.tool_calls)
            )
            executions = await asyncio.gather(
                *(
                    self._registry.execute(
                        InvocationContext(
                            tool_name=tc.name,
                            arguments=tc.arguments,
                            task_id=task_id,
                            subtask_id=subtask_id,
                        )
                    )
                    for tc in response.tool_calls
                )
            )
            results = [
                ToolResult(
                    tool_call_id=tc.id,
                    name=tc.name,
                    output=ex.output if ex.success else (ex.error or "tool failed"),
                    is_error=not ex.success,
                )
                for tc, ex in zip(response.tool_calls, executions, strict=True)
            ]
            conversation.append(ToolResultMessage(results=results))

        output, _ = _extract_confidence(last_text)
        return self._result(
            subtask_id,
            output=output,
            confidence=_EXHAUSTED_CONFIDENCE,
            error=f"turn budget exhausted after {self._max_turns} turns",
        )

    def _result(
        self, subtask_id: str, *, output: str, confidence: float, error: str | None = None
    ) -> AgentResult:
        return AgentResult(
            subtask_id=subtask_id,
            specialist=self._specialist,
            output=output,
            confidence=confidence,
            error=error,
        )
