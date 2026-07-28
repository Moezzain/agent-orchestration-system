"""Tool contract: a name, a Pydantic args schema, and an async execute.

The args model is the single source of truth — it validates LLM-supplied
arguments at runtime AND exports the JSON schema the LLM sees. Field
descriptions are prompt material, same discipline as the domain schemas.
"""

from abc import ABC, abstractmethod
from typing import Any, ClassVar
from uuid import UUID

from pydantic import BaseModel, ConfigDict

from orchestrator.llm.types import ToolSpec


class Tool[ArgsT: BaseModel](ABC):
    """Subclass with a concrete args model and execute() implementation."""

    name: ClassVar[str]
    description: ClassVar[str]
    # Sensitive tools trigger human approval before running (HITL, Phase 6)
    sensitive: ClassVar[bool] = False

    args_model: type[ArgsT]

    @abstractmethod
    async def execute(self, args: ArgsT) -> str: ...

    def to_spec(self) -> ToolSpec:
        """Export as the provider-neutral spec; adapters convert to dialects."""
        return ToolSpec(
            name=self.name,
            description=self.description,
            input_schema=self.args_model.model_json_schema(),
        )


class InvocationContext(BaseModel):
    """Who is calling what — threaded through middleware for logging/limits."""

    model_config = ConfigDict(frozen=True)

    tool_name: str
    arguments: dict[str, Any]
    task_id: UUID | None = None
    subtask_id: str | None = None


class ToolExecutionResult(BaseModel):
    """Every invocation returns this — errors are data, not exceptions.

    Failures flow back to the LLM as tool output so it can retry or adapt;
    raising would kill the whole graph run over one bad tool call.
    """

    model_config = ConfigDict(frozen=True)

    output: str
    success: bool
    error: str | None = None
    latency_ms: int
