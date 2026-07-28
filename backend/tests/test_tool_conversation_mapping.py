"""Tool-conversation wire-format mapping — the multi-turn tool-loop contract.

Same neutral conversation, two very different provider encodings. These tests
ARE the documentation of that difference.

Assertions access TypedDict unions loosely (via Any) — we're checking wire
shapes, not exercising the type system.
"""

import json
from typing import Any

from orchestrator.llm.anthropic_provider import _to_message_params as to_anthropic
from orchestrator.llm.openai_provider import _to_message_params as to_openai
from orchestrator.llm.types import (
    AssistantToolCallMessage,
    ChatMessage,
    ConversationMessage,
    ToolCall,
    ToolResult,
    ToolResultMessage,
)

CONVERSATION: list[ConversationMessage] = [
    ChatMessage(role="user", content="What's 2+2? Use the calculator."),
    AssistantToolCallMessage(
        text="I'll compute that.",
        tool_calls=[ToolCall(id="tc_1", name="run_python", arguments={"code": "print(2+2)"})],
    ),
    ToolResultMessage(results=[ToolResult(tool_call_id="tc_1", name="run_python", output="4")]),
]


class TestAnthropicMapping:
    def test_assistant_tool_calls_become_content_blocks(self) -> None:
        params: Any = to_anthropic(CONVERSATION)

        assistant = params[1]
        assert assistant["role"] == "assistant"
        blocks = assistant["content"]
        assert blocks[0] == {"type": "text", "text": "I'll compute that."}
        assert blocks[1]["type"] == "tool_use"
        assert blocks[1]["input"] == {"code": "print(2+2)"}

    def test_tool_results_become_user_content_blocks(self) -> None:
        params: Any = to_anthropic(CONVERSATION)

        results_msg = params[2]
        assert results_msg["role"] == "user"  # Anthropic quirk: results ride a user turn
        block = results_msg["content"][0]
        assert block["type"] == "tool_result"
        assert block["tool_use_id"] == "tc_1"
        assert block["content"] == "4"


class TestOpenAIMapping:
    def test_assistant_tool_calls_ride_dedicated_field(self) -> None:
        params: Any = to_openai(CONVERSATION, system=None)

        assistant = params[1]
        assert assistant["role"] == "assistant"
        tool_call = assistant["tool_calls"][0]
        assert tool_call["id"] == "tc_1"
        # OpenAI wants arguments re-serialized as a JSON string
        assert json.loads(tool_call["function"]["arguments"]) == {"code": "print(2+2)"}

    def test_each_result_becomes_a_tool_role_message(self) -> None:
        params: Any = to_openai(CONVERSATION, system=None)

        tool_msg = params[2]
        assert tool_msg["role"] == "tool"
        assert tool_msg["tool_call_id"] == "tc_1"
        assert tool_msg["content"] == "4"

    def test_multiple_results_fan_out_to_multiple_messages(self) -> None:
        conversation: list[ConversationMessage] = [
            ToolResultMessage(
                results=[
                    ToolResult(tool_call_id="a", name="t1", output="1"),
                    ToolResult(tool_call_id="b", name="t2", output="2"),
                ]
            )
        ]

        params: Any = to_openai(conversation, system=None)

        assert [p["role"] for p in params] == ["tool", "tool"]
