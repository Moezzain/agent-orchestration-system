from pydantic import BaseModel, Field

from orchestrator.agents import AgentRuntime
from orchestrator.llm.types import (
    AssistantToolCallMessage,
    ToolCall,
    ToolResultMessage,
)
from orchestrator.schemas import SpecialistType
from orchestrator.tools import Tool, ToolRegistry
from tests.fakes import ScriptedProvider, text_response, tool_call_response


class EchoArgs(BaseModel):
    text: str = Field(description="Text to echo.")


class EchoTool(Tool[EchoArgs]):
    name = "echo"
    description = "Echoes text."
    args_model = EchoArgs

    async def execute(self, args: EchoArgs) -> str:
        return f"echo:{args.text}"


def _runtime(provider: ScriptedProvider, **overrides: object) -> AgentRuntime:
    registry = ToolRegistry()
    registry.register(EchoTool())
    defaults: dict[str, object] = {
        "provider": provider,
        "model": "fake-model",
        "specialist": SpecialistType.RESEARCH,
        "system_prompt": "You are a research agent.",
        "registry": registry,
        "tool_names": ["echo"],
    }
    return AgentRuntime(**{**defaults, **overrides})  # type: ignore[arg-type]


async def test_direct_answer_with_confidence_marker() -> None:
    provider = ScriptedProvider(["The answer is 4.\nCONFIDENCE: 0.85"])

    result = await _runtime(provider).run("what is 2+2", subtask_id="s1")

    assert result.output == "The answer is 4."  # marker stripped
    assert result.confidence == 0.85
    assert result.error is None
    assert result.specialist is SpecialistType.RESEARCH


async def test_missing_confidence_defaults() -> None:
    provider = ScriptedProvider(["Just an answer."])

    result = await _runtime(provider).run("task", subtask_id="s1")

    assert result.confidence == 0.5


async def test_tool_loop_executes_and_feeds_results_back() -> None:
    provider = ScriptedProvider(
        [
            tool_call_response(
                ToolCall(id="tc1", name="echo", arguments={"text": "hello"}),
                text="Let me check.",
            ),
            "Done: hello was echoed.\nCONFIDENCE: 0.9",
        ]
    )

    result = await _runtime(provider).run("echo hello", subtask_id="s1")

    assert result.output == "Done: hello was echoed."
    # second LLM call saw: user msg, assistant echo of its own call, tool results
    second_call = provider.calls[1]
    assert isinstance(second_call[1], AssistantToolCallMessage)
    assert second_call[1].tool_calls[0].name == "echo"
    results_msg = second_call[2]
    assert isinstance(results_msg, ToolResultMessage)
    assert results_msg.results[0].output == "echo:hello"
    assert results_msg.results[0].is_error is False


async def test_tool_failure_becomes_error_result_in_conversation() -> None:
    provider = ScriptedProvider(
        [
            tool_call_response(ToolCall(id="tc1", name="echo", arguments={"wrong_field": True})),
            "I hit an error and adjusted.\nCONFIDENCE: 0.4",
        ]
    )

    result = await _runtime(provider).run("task", subtask_id="s1")

    results_msg = provider.calls[1][2]
    assert isinstance(results_msg, ToolResultMessage)
    assert results_msg.results[0].is_error is True
    assert "invalid arguments" in results_msg.results[0].output
    assert result.error is None  # the agent recovered; the run itself succeeded


async def test_parallel_tool_calls_all_execute() -> None:
    provider = ScriptedProvider(
        [
            tool_call_response(
                ToolCall(id="a", name="echo", arguments={"text": "one"}),
                ToolCall(id="b", name="echo", arguments={"text": "two"}),
            ),
            "Both done.\nCONFIDENCE: 0.8",
        ]
    )

    await _runtime(provider).run("task", subtask_id="s1")

    results_msg = provider.calls[1][2]
    assert isinstance(results_msg, ToolResultMessage)
    assert [r.output for r in results_msg.results] == ["echo:one", "echo:two"]
    assert [r.tool_call_id for r in results_msg.results] == ["a", "b"]


async def test_turn_budget_exhaustion_flagged() -> None:
    endless = [
        tool_call_response(ToolCall(id=f"t{i}", name="echo", arguments={"text": "x"}))
        for i in range(3)
    ]
    provider = ScriptedProvider(endless)  # never gives a final answer

    result = await _runtime(provider, max_turns=3).run("task", subtask_id="s1")

    assert result.error is not None and "turn budget exhausted" in result.error
    assert result.confidence == 0.2


async def test_refusal_short_circuits() -> None:
    provider = ScriptedProvider([text_response("", stop_reason="refusal")])

    result = await _runtime(provider).run("task", subtask_id="s1")

    assert result.error == "model refused the task"
    assert result.confidence == 0.0


async def test_system_prompt_carries_confidence_instructions() -> None:
    provider = ScriptedProvider(["ok"])

    await _runtime(provider).run("task", subtask_id="s1")

    assert provider.systems[0] is not None
    assert "You are a research agent." in provider.systems[0]
    assert "CONFIDENCE:" in provider.systems[0]
