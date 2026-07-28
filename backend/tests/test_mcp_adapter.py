"""MCP adapter tests — real MCP client/server over in-memory streams.

No network, no subprocess: an MCPServer with two tools runs in a task group,
our adapter discovers and executes through the full registry pipeline.
"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import anyio
from mcp.client.session import ClientSession
from mcp.server.mcpserver import MCPServer
from mcp.shared.memory import create_client_server_memory_streams

from orchestrator.tools import InvocationContext, ToolRegistry
from orchestrator.tools.mcp_adapter import discover_mcp_tools


def _demo_server() -> MCPServer:
    server = MCPServer("demo")

    @server.tool()
    def add(a: int, b: int) -> int:
        """Add two integers."""
        return a + b

    @server.tool()
    def shout(text: str) -> str:
        """Uppercase the text."""
        return text.upper()

    return server


@asynccontextmanager
async def _connected_session() -> AsyncIterator[ClientSession]:
    server = _demo_server()
    low = server._lowlevel_server  # noqa: SLF001 — the documented test seam
    async with (
        create_client_server_memory_streams() as (client_streams, server_streams),
        anyio.create_task_group() as tg,
    ):

        async def run_server() -> None:
            await low.run(
                server_streams[0],
                server_streams[1],
                low.create_initialization_options(),
                raise_exceptions=True,
            )

        tg.start_soon(run_server)
        async with ClientSession(client_streams[0], client_streams[1]) as session:
            await session.initialize()
            yield session
        tg.cancel_scope.cancel()


async def test_discovery_wraps_tools_with_server_schema() -> None:
    async with _connected_session() as session:
        tools = await discover_mcp_tools(session)

        names = {t.name for t in tools}
        assert names == {"mcp_add", "mcp_shout"}
        add_tool = next(t for t in tools if t.name == "mcp_add")
        spec = add_tool.to_spec()
        # the LLM sees the SERVER's schema, not the permissive passthrough
        properties: dict[str, dict[str, object]] = spec.input_schema["properties"]  # type: ignore[assignment]
        assert properties["a"]["type"] == "integer"
        assert spec.description.startswith("[MCP] ")


async def test_execution_through_registry_pipeline() -> None:
    async with _connected_session() as session:
        registry = ToolRegistry()
        for tool in await discover_mcp_tools(session):
            registry.register(tool)

        result = await registry.execute(
            InvocationContext(tool_name="mcp_add", arguments={"a": 19, "b": 23})
        )

        assert result.success
        assert result.output == "42"


async def test_server_side_validation_error_is_llm_readable() -> None:
    async with _connected_session() as session:
        registry = ToolRegistry()
        for tool in await discover_mcp_tools(session):
            registry.register(tool)

        # bad types pass our permissive model; the MCP SERVER rejects them
        result = await registry.execute(
            InvocationContext(tool_name="mcp_add", arguments={"a": "nope", "b": 3})
        )

        assert not result.success
        assert result.error is not None
        assert "validation error" in result.error


async def test_registry_specs_export_both_mcp_tools() -> None:
    async with _connected_session() as session:
        registry = ToolRegistry()
        for tool in await discover_mcp_tools(session):
            registry.register(tool)

        specs = registry.specs(["mcp_add", "mcp_shout"])

        assert len(specs) == 2
        shout = next(s for s in specs if s.name == "mcp_shout")
        assert shout.input_schema["required"] == ["text"]
