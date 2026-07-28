"""Sensitive-tool gate: FAIL-CLOSED tool middleware.

Every other middleware in this codebase fails open (logging, rate limits,
events) because they're observability. This one is a security control, so
the asymmetry flips: no explicit approval -> the tool does not run, period.

Why deny-with-message instead of interrupt(): tool calls happen mid-work-node,
after LLM calls. An interrupt there would re-execute those LLM calls on
resume (the step-27 footgun) — possibly producing DIFFERENT tool calls than
the ones the human approved. Denial keeps the gate safe by construction: the
agent is told approval is missing, finishes its attempt, and the normal
result-escalation path brings a human in with full context.
"""

from orchestrator.logging import get_logger
from orchestrator.tools.base import InvocationContext, ToolExecutionResult
from orchestrator.tools.registry import CallNext

logger = get_logger(__name__)


class SensitiveToolGate:
    """Denies sensitive tools unless the task carries an explicit allowance.

    Allowances are per (task, tool) and are granted by a human decision —
    plumbed in step 29's API; until then, sensitive tools are simply blocked.
    """

    def __init__(
        self,
        sensitive_tools: frozenset[str],
        allowed: frozenset[tuple[str, str]] = frozenset(),
    ) -> None:
        self._sensitive = sensitive_tools
        self._allowed = allowed  # {(task_id_str, tool_name), ...}

    async def __call__(
        self, context: InvocationContext, call_next: CallNext
    ) -> ToolExecutionResult:
        if context.tool_name not in self._sensitive:
            return await call_next(context)

        key = (str(context.task_id), context.tool_name)
        if key in self._allowed:
            logger.info(
                "sensitive_tool_allowed", tool=context.tool_name, task_id=str(context.task_id)
            )
            return await call_next(context)

        logger.info("sensitive_tool_denied", tool=context.tool_name, task_id=str(context.task_id))
        return ToolExecutionResult(
            output="",
            success=False,
            error=(
                f"{context.tool_name!r} is a sensitive tool and requires human approval, "
                "which has not been granted for this task. Do not retry it; complete "
                "what you can without it and state clearly what could not be done."
            ),
            latency_ms=0,
        )
