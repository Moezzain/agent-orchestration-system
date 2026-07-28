"""Provider-neutral LLM types.

Everything above the adapter layer speaks these shapes. Each provider adapter
normalizes its SDK's request/response format into them exactly once — provider
drift (tool-call shapes, usage fields, system-prompt placement) stays inside
the adapters.
"""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

Role = Literal["user", "assistant"]


class ChatMessage(BaseModel):
    """One conversation turn. System prompts are passed separately, not as a role."""

    model_config = ConfigDict(frozen=True)

    role: Role
    content: str


class ToolSpec(BaseModel):
    """Provider-neutral tool definition; adapters convert to each provider's dialect."""

    model_config = ConfigDict(frozen=True)

    name: str
    description: str
    input_schema: dict[str, object]


class ToolCall(BaseModel):
    """A tool invocation the model requested."""

    model_config = ConfigDict(frozen=True)

    id: str
    name: str
    arguments: dict[str, object]


class AssistantToolCallMessage(BaseModel):
    """Assistant turn that requested tool calls — echoed back into the
    conversation so the model sees its own requests on the next turn."""

    model_config = ConfigDict(frozen=True)

    text: str = ""
    tool_calls: list[ToolCall]


class ToolResult(BaseModel):
    """Outcome of one tool call, linked to the request by tool_call_id."""

    model_config = ConfigDict(frozen=True)

    tool_call_id: str
    name: str
    output: str
    is_error: bool = False


class ToolResultMessage(BaseModel):
    """All results for the preceding assistant tool-call turn, as one message."""

    model_config = ConfigDict(frozen=True)

    results: list[ToolResult]


# What a conversation is made of. Adapters map each variant to their wire format.
ConversationMessage = ChatMessage | AssistantToolCallMessage | ToolResultMessage


class Usage(BaseModel):
    model_config = ConfigDict(frozen=True)

    input_tokens: int
    output_tokens: int


StopReason = Literal["end_turn", "tool_use", "max_tokens", "refusal", "other"]


class LLMResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    text: str
    tool_calls: list[ToolCall] = Field(default_factory=list)
    usage: Usage
    model: str
    provider: str
    stop_reason: StopReason
    latency_ms: int
