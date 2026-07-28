"""OpenAI adapter mapping tests — fixture-based, no network."""

import pytest
from openai.types.chat import ChatCompletion, ChatCompletionMessage
from openai.types.chat.chat_completion import Choice
from openai.types.chat.chat_completion_message_function_tool_call import (
    ChatCompletionMessageFunctionToolCall,
    Function,
)
from openai.types.completion_usage import CompletionUsage

from orchestrator.llm import ChatMessage, ToolSpec
from orchestrator.llm.openai_provider import (
    _parse_response,
    _to_message_params,
    _to_tool_params,
)


def _raw_completion(
    message: ChatCompletionMessage,
    finish_reason: str = "stop",
) -> ChatCompletion:
    return ChatCompletion.model_construct(
        id="chatcmpl_test",
        object="chat.completion",
        created=0,
        model="gpt-5-mini",
        choices=[Choice.model_construct(index=0, message=message, finish_reason=finish_reason)],
        usage=CompletionUsage.model_construct(
            prompt_tokens=200, completion_tokens=50, total_tokens=250
        ),
    )


class TestRequestMapping:
    def test_system_becomes_a_message_role(self) -> None:
        # The contrast with Anthropic: system is a message here, not a param
        params = _to_message_params([ChatMessage(role="user", content="hi")], system="Be terse.")
        assert params[0] == {"role": "system", "content": "Be terse."}
        assert params[1] == {"role": "user", "content": "hi"}

    def test_no_system_means_no_system_message(self) -> None:
        params = _to_message_params([ChatMessage(role="user", content="hi")], system=None)
        assert params == [{"role": "user", "content": "hi"}]

    def test_tools_map_to_function_dialect(self) -> None:
        spec = ToolSpec(name="web_search", description="Search", input_schema={"type": "object"})
        params = _to_tool_params([spec])
        assert params[0]["type"] == "function"
        assert params[0]["function"]["name"] == "web_search"


class TestResponseParsing:
    def test_text_response(self) -> None:
        raw = _raw_completion(
            ChatCompletionMessage.model_construct(role="assistant", content="Hello.")
        )

        response = _parse_response(raw, latency_ms=90)

        assert response.text == "Hello."
        assert response.usage.input_tokens == 200  # prompt_tokens, normalized
        assert response.usage.output_tokens == 50  # completion_tokens, normalized
        assert response.provider == "openai"
        assert response.stop_reason == "end_turn"  # "stop", normalized

    def test_tool_call_arguments_json_string_is_parsed(self) -> None:
        # The other contrast: OpenAI sends tool args as a JSON STRING
        raw = _raw_completion(
            ChatCompletionMessage.model_construct(
                role="assistant",
                content=None,
                tool_calls=[
                    ChatCompletionMessageFunctionToolCall.model_construct(
                        id="call_1",
                        type="function",
                        function=Function.model_construct(
                            name="web_search", arguments='{"query": "chroma vs pgvector"}'
                        ),
                    )
                ],
            ),
            finish_reason="tool_calls",
        )

        response = _parse_response(raw, latency_ms=50)

        assert response.text == ""
        assert response.tool_calls[0].arguments == {"query": "chroma vs pgvector"}
        assert response.stop_reason == "tool_use"

    def test_malformed_tool_arguments_raise_with_context(self) -> None:
        raw = _raw_completion(
            ChatCompletionMessage.model_construct(
                role="assistant",
                content=None,
                tool_calls=[
                    ChatCompletionMessageFunctionToolCall.model_construct(
                        id="call_1",
                        type="function",
                        function=Function.model_construct(
                            name="web_search", arguments='{"query": '
                        ),
                    )
                ],
            ),
            finish_reason="tool_calls",
        )

        with pytest.raises(ValueError, match="malformed JSON for tool 'web_search'"):
            _parse_response(raw, latency_ms=1)

    def test_content_filter_maps_to_refusal(self) -> None:
        raw = _raw_completion(
            ChatCompletionMessage.model_construct(role="assistant", content=""),
            finish_reason="content_filter",
        )
        assert _parse_response(raw, latency_ms=1).stop_reason == "refusal"
