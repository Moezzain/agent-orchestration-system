"""MCP adapter: expose an MCP server's tools as registry entries.

MCP (Model Context Protocol) servers publish tools with JSON-schema inputs.
This adapter lists them over an active ClientSession and wraps each as a
Tool, so specialists use external MCP tools exactly like builtins — same
registry, same middleware (logging, rate limits, sensitive gate).

Validation split, stated honestly: our registry normally validates args with
the tool's own Pydantic model. MCP schemas arrive as JSON schema at runtime,
so MCPTool passes arguments through permissively and the MCP SERVER enforces
its schema — its validation errors flow back through the normal error-result
channel (LLM-readable, self-correctable). to_spec() is overridden to hand the
LLM the server's schema verbatim, so the model still sees full typing.
"""

from typing import Any, ClassVar

from mcp.client.session import ClientSession
from pydantic import BaseModel, ConfigDict

from orchestrator.llm.types import ToolSpec
from orchestrator.tools.base import Tool

_OUTPUT_CAP = 20_000


class MCPArgs(BaseModel):
    """Permissive pass-through: the MCP server owns schema enforcement."""

    model_config = ConfigDict(extra="allow")


class MCPTool(Tool[MCPArgs]):
    args_model = MCPArgs
    # placeholders — instances carry the real values (set in __init__)
    name: ClassVar[str] = "__mcp__"
    description: ClassVar[str] = ""

    def __init__(
        self,
        session: ClientSession,
        *,
        name: str,
        description: str,
        input_schema: dict[str, Any],
        prefix: str = "mcp",
    ) -> None:
        self._session = session
        self._remote_name = name
        self._schema = input_schema
        # instance attrs shadow the ClassVar placeholders (runtime-configured
        # identity is the exception to class-level tool identity)
        self.name = f"{prefix}_{name}"  # type: ignore[misc]
        self.description = f"[MCP] {description}"  # type: ignore[misc]

    def to_spec(self) -> ToolSpec:
        # the server's schema goes to the LLM verbatim, not MCPArgs' empty one
        return ToolSpec(name=self.name, description=self.description, input_schema=self._schema)

    async def execute(self, args: MCPArgs) -> str:
        result = await self._session.call_tool(
            self._remote_name, args.model_dump(exclude_none=False)
        )
        # content is a union of block types; only text blocks carry .text
        from mcp.types import TextContent

        texts = [c.text for c in result.content if isinstance(c, TextContent)]
        output = "\n".join(texts)[:_OUTPUT_CAP]
        if result.is_error:
            # server-side failure (incl. its schema validation) -> our normal
            # error-result channel via the registry's exception handling
            raise ValueError(output or f"MCP tool {self._remote_name!r} failed")
        return output


async def discover_mcp_tools(session: ClientSession, *, prefix: str = "mcp") -> list[MCPTool]:
    """List the server's tools and wrap each. Call after session.initialize()."""
    listing = await session.list_tools()
    return [
        MCPTool(
            session,
            name=t.name,
            description=t.description or t.name,
            input_schema=t.input_schema,
            prefix=prefix,
        )
        for t in listing.tools
    ]
