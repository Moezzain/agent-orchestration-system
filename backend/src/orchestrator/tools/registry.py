"""Tool registry: registration, spec export, validated execution, middleware.

Execution pipeline:  validate args → middleware chain → tool.execute
Middleware (logging, rate limits — step 11) wraps execution onion-style,
same mental model as Express/Koa middleware.
"""

import time
from collections.abc import Awaitable, Callable
from typing import Any

from opentelemetry import trace
from pydantic import ValidationError

from orchestrator.llm.types import ToolSpec
from orchestrator.observability.otel import KIND_ATTR, TASK_ID_ATTR
from orchestrator.tools.base import InvocationContext, Tool, ToolExecutionResult

CallNext = Callable[[InvocationContext], Awaitable[ToolExecutionResult]]
Middleware = Callable[[InvocationContext, CallNext], Awaitable[ToolExecutionResult]]


def _failure(error: str, *, started: float) -> ToolExecutionResult:
    return ToolExecutionResult(
        output="",
        success=False,
        error=error,
        latency_ms=int((time.perf_counter() - started) * 1000),
    )


class ToolRegistry:
    def __init__(self, middlewares: list[Middleware] | None = None) -> None:
        self._tools: dict[str, Tool[Any]] = {}
        self._middlewares = middlewares or []

    def register(self, tool: Tool[Any]) -> None:
        if tool.name in self._tools:
            raise ValueError(f"tool {tool.name!r} already registered")
        self._tools[tool.name] = tool

    def get(self, name: str) -> Tool[Any] | None:
        return self._tools.get(name)

    def specs(self, names: list[str] | None = None) -> list[ToolSpec]:
        """Specs for the given tool names (or all) — what an agent's LLM sees."""
        tools = (
            self._tools.values()
            if names is None
            else [self._tools[n] for n in names if n in self._tools]
        )
        return [t.to_spec() for t in tools]

    async def execute(self, context: InvocationContext) -> ToolExecutionResult:
        """Validate and run. Never raises for tool-level failures — the error
        result goes back to the LLM, which can correct itself and retry."""
        tracer = trace.get_tracer("orchestrator.tools")
        with tracer.start_as_current_span(f"tool.{context.tool_name}") as span:
            span.set_attribute(KIND_ATTR, "tool_call")
            if context.task_id is not None:
                span.set_attribute(TASK_ID_ATTR, str(context.task_id))
            if context.subtask_id is not None:
                span.set_attribute("tool.subtask_id", context.subtask_id)
            result = await self._execute_inner(context)
            span.set_attribute("tool.success", result.success)
            span.set_attribute("tool.latency_ms", result.latency_ms)
            return result

    async def _execute_inner(self, context: InvocationContext) -> ToolExecutionResult:
        started = time.perf_counter()

        tool = self._tools.get(context.tool_name)
        if tool is None:
            return _failure(
                f"unknown tool {context.tool_name!r}; available: {sorted(self._tools)}",
                started=started,
            )

        try:
            args = tool.args_model.model_validate(context.arguments)
        except ValidationError as exc:
            return _failure(f"invalid arguments for {context.tool_name!r}: {exc}", started=started)

        async def run(ctx: InvocationContext) -> ToolExecutionResult:
            try:
                output = await tool.execute(args)
            except Exception as exc:  # noqa: BLE001 — tool bugs become results, not crashes
                return _failure(f"{context.tool_name!r} failed: {exc}", started=started)
            return ToolExecutionResult(
                output=output,
                success=True,
                latency_ms=int((time.perf_counter() - started) * 1000),
            )

        # Build the onion: last-registered middleware is innermost
        handler: CallNext = run
        for middleware in reversed(self._middlewares):
            handler = _wrap(middleware, handler)
        return await handler(context)


def _wrap(middleware: Middleware, next_handler: CallNext) -> CallNext:
    async def wrapped(ctx: InvocationContext) -> ToolExecutionResult:
        return await middleware(ctx, next_handler)

    return wrapped
