import pytest
from pydantic import BaseModel, Field

from orchestrator.tools import (
    CallNext,
    InvocationContext,
    Tool,
    ToolExecutionResult,
    ToolRegistry,
)


class EchoArgs(BaseModel):
    text: str = Field(description="Text to echo back.")
    repeat: int = Field(default=1, ge=1, le=3, description="How many times.")


class EchoTool(Tool[EchoArgs]):
    name = "echo"
    description = "Echoes text back."
    args_model = EchoArgs

    async def execute(self, args: EchoArgs) -> str:
        return " ".join([args.text] * args.repeat)


class ExplodingTool(Tool[EchoArgs]):
    name = "exploding"
    description = "Always raises."
    args_model = EchoArgs
    sensitive = True

    async def execute(self, args: EchoArgs) -> str:
        raise RuntimeError("boom")


def _ctx(tool_name: str, arguments: dict[str, object]) -> InvocationContext:
    return InvocationContext(tool_name=tool_name, arguments=arguments)


@pytest.fixture
def registry() -> ToolRegistry:
    r = ToolRegistry()
    r.register(EchoTool())
    r.register(ExplodingTool())
    return r


class TestRegistration:
    def test_duplicate_name_rejected(self, registry: ToolRegistry) -> None:
        with pytest.raises(ValueError, match="already registered"):
            registry.register(EchoTool())

    def test_specs_export_json_schema_with_descriptions(self, registry: ToolRegistry) -> None:
        (spec,) = registry.specs(names=["echo"])

        assert spec.name == "echo"
        schema_props = spec.input_schema["properties"]
        assert schema_props["text"]["description"] == "Text to echo back."  # type: ignore[index]

    def test_sensitive_flag_readable(self, registry: ToolRegistry) -> None:
        tool = registry.get("exploding")
        assert tool is not None and tool.sensitive is True


class TestExecution:
    async def test_valid_call_succeeds(self, registry: ToolRegistry) -> None:
        result = await registry.execute(_ctx("echo", {"text": "hi", "repeat": 2}))

        assert result.success
        assert result.output == "hi hi"

    async def test_invalid_args_become_error_result(self, registry: ToolRegistry) -> None:
        # repeat=10 violates le=3 — LLM gets the error back, no exception
        result = await registry.execute(_ctx("echo", {"text": "hi", "repeat": 10}))

        assert not result.success
        assert result.error is not None and "invalid arguments" in result.error

    async def test_unknown_tool_becomes_error_result(self, registry: ToolRegistry) -> None:
        result = await registry.execute(_ctx("hallucinated_tool", {}))

        assert not result.success
        assert result.error is not None and "unknown tool" in result.error
        assert "echo" in result.error  # available tools listed for the LLM

    async def test_tool_exception_becomes_error_result(self, registry: ToolRegistry) -> None:
        result = await registry.execute(_ctx("exploding", {"text": "x"}))

        assert not result.success
        assert result.error is not None and "boom" in result.error


class TestMiddleware:
    async def test_middleware_wraps_execution_in_order(self) -> None:
        calls: list[str] = []

        def make(tag: str):  # type: ignore[no-untyped-def]
            async def mw(ctx: InvocationContext, call_next: CallNext) -> ToolExecutionResult:
                calls.append(f"{tag}:before")
                result = await call_next(ctx)
                calls.append(f"{tag}:after")
                return result

            return mw

        registry = ToolRegistry(middlewares=[make("outer"), make("inner")])
        registry.register(EchoTool())

        result = await registry.execute(_ctx("echo", {"text": "hi"}))

        assert result.success
        assert calls == ["outer:before", "inner:before", "inner:after", "outer:after"]

    async def test_middleware_can_short_circuit(self) -> None:
        async def deny(ctx: InvocationContext, call_next: CallNext) -> ToolExecutionResult:
            return ToolExecutionResult(
                output="", success=False, error="denied by policy", latency_ms=0
            )

        registry = ToolRegistry(middlewares=[deny])
        registry.register(EchoTool())

        result = await registry.execute(_ctx("echo", {"text": "hi"}))

        assert not result.success
        assert result.error == "denied by policy"
