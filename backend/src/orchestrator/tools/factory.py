"""Default tool registry construction — shared by CLI and worker."""

from pathlib import Path

from orchestrator.config import Settings
from orchestrator.tools.builtin import (
    CodeExecutionTool,
    FileReadTool,
    FileWriteTool,
    WebSearchTool,
)
from orchestrator.tools.registry import Middleware, ToolRegistry


def build_default_registry(
    settings: Settings, *, middlewares: list[Middleware] | None = None
) -> ToolRegistry:
    registry = ToolRegistry(middlewares=middlewares)
    workspace = Path(settings.workspace_dir)
    registry.register(FileReadTool(workspace))
    registry.register(FileWriteTool(workspace))
    registry.register(CodeExecutionTool())
    if settings.tavily_api_key is not None:
        registry.register(WebSearchTool(settings.tavily_api_key.get_secret_value()))
    return registry
