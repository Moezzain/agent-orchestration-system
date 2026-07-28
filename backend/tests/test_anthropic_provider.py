"""Adapter mapping tests — fixture-based, no network.

We construct real Anthropic SDK response objects and assert our normalization,
so provider-drift bugs (renamed fields, changed shapes) surface as test
failures on SDK upgrades.
"""

from anthropic.types import Message as AnthropicMessage
from anthropic.types import TextBlock, ToolUseBlock
from anthropic.types import Usage as AnthropicUsage

from orchestrator.llm import ChatMessage, ToolSpec
from orchestrator.llm.anthropic_provider import (
    _parse_response,
    _to_message_params,
    _to_tool_params,
)


def _raw_message(
    content: list[TextBlock | ToolUseBlock],
    stop_reason: str = "end_turn",
) -> AnthropicMessage:
    return AnthropicMessage.model_construct(
        id="msg_test",
        type="message",
        role="assistant",
        model="claude-haiku-4-5",
        content=content,
        stop_reason=stop_reason,
        stop_sequence=None,
        usage=AnthropicUsage.model_construct(input_tokens=100, output_tokens=25),
    )


class TestRequestMapping:
    def test_messages_map_to_role_content_dicts(self) -> None:
        params = _to_message_params(
            [
                ChatMessage(role="user", content="hi"),
                ChatMessage(role="assistant", content="hello"),
            ]
        )
        assert params == [
            {"role": "user", "content": "hi"},
            {"role": "assistant", "content": "hello"},
        ]

    def test_tools_map_to_anthropic_dialect(self) -> None:
        spec = ToolSpec(
            name="web_search",
            description="Search the web",
            input_schema={"type": "object", "properties": {"query": {"type": "string"}}},
        )
        params = _to_tool_params([spec])
        assert params[0]["name"] == "web_search"
        assert params[0]["input_schema"]["type"] == "object"


class TestResponseParsing:
    def test_text_response(self) -> None:
        raw = _raw_message([TextBlock.model_construct(type="text", text="The answer is 4.")])

        response = _parse_response(raw, latency_ms=120)

        assert response.text == "The answer is 4."
        assert response.tool_calls == []
        assert response.usage.input_tokens == 100
        assert response.usage.output_tokens == 25
        assert response.provider == "anthropic"
        assert response.stop_reason == "end_turn"
        assert response.latency_ms == 120

    def test_tool_use_response(self) -> None:
        raw = _raw_message(
            [
                TextBlock.model_construct(type="text", text="Let me search."),
                ToolUseBlock.model_construct(
                    type="tool_use",
                    id="toolu_1",
                    name="web_search",
                    input={"query": "quantum error correction"},
                ),
            ],
            stop_reason="tool_use",
        )

        response = _parse_response(raw, latency_ms=80)

        assert response.text == "Let me search."
        assert len(response.tool_calls) == 1
        assert response.tool_calls[0].name == "web_search"
        assert response.tool_calls[0].arguments == {"query": "quantum error correction"}
        assert response.stop_reason == "tool_use"

    def test_unknown_stop_reason_maps_to_other(self) -> None:
        raw = _raw_message(
            [TextBlock.model_construct(type="text", text="x")],
            stop_reason="pause_turn",
        )

        assert _parse_response(raw, latency_ms=1).stop_reason == "other"
