"""Step 28: notify level (non-blocking) + sensitive-tool gate (fail-closed)."""

from typing import Any
from uuid import uuid4

from langchain_core.runnables import RunnableConfig
from pydantic import BaseModel, Field

from orchestrator.graph.build import build_graph
from orchestrator.graph.nodes import GraphDeps
from orchestrator.graph.state import initial_state
from orchestrator.hitl import EscalationPolicy, SensitiveToolGate
from orchestrator.observability.events import EventType, TaskEvent
from orchestrator.schemas import EscalationReason, Task
from orchestrator.tools import InvocationContext, Tool, ToolRegistry
from tests.test_graph import FakeSpecialistRunner, FakeSupervisor, _accept_all, _chain_plan


class _Args(BaseModel):
    to: str = Field(default="x@y.z")


class SendEmailTool(Tool[_Args]):
    name = "send_email"
    description = "Sends an email."
    args_model = _Args
    sensitive = True

    def __init__(self) -> None:
        self.sent = 0

    async def execute(self, args: _Args) -> str:
        self.sent += 1
        return f"sent to {args.to}"


class TestNotifyLevel:
    async def test_low_confidence_notifies_without_pausing(self) -> None:
        # default policy: LOW_CONFIDENCE is a notify_reason
        class LowConfidenceRunner(FakeSpecialistRunner):
            async def __call__(self, subtask, *, context, task):  # type: ignore[no-untyped-def]
                result = await super().__call__(subtask, context=context, task=task)
                return result.model_copy(update={"confidence": 0.05})

        events: list[TaskEvent] = []

        async def collect(event: TaskEvent) -> None:
            events.append(event)

        deps = GraphDeps(
            supervisor=FakeSupervisor(_chain_plan()),
            reviewer=_accept_all("s1", "s2"),
            run_specialist=LowConfidenceRunner(),
            emit=collect,
            policy=EscalationPolicy(confidence_floor=0.5),  # default notify_reasons
        )
        graph = build_graph(deps)
        config: RunnableConfig = {"configurable": {"thread_id": str(uuid4())}}

        final: dict[str, Any] = await graph.ainvoke(
            initial_state(Task(request="notify test")), config
        )

        # no pause: ran to completion; human informed via event, not blocked
        assert "__interrupt__" not in final
        assert final["final_output"] is not None
        assert final["subtask_status"]["s1"] == "accepted_with_issues"
        notify_events = [e for e in events if e.type is EventType.ESCALATION_RAISED]
        assert len(notify_events) == 2  # s1 and s2 both low-confidence
        assert notify_events[0].data["level"] == "notify"
        assert notify_events[0].data["reason"] == "low_confidence"

    def test_notify_reasons_configurable_back_to_blocking(self) -> None:
        policy = EscalationPolicy(notify_reasons=frozenset())
        from orchestrator.schemas import ApprovalLevel

        assert policy.level_for(EscalationReason.LOW_CONFIDENCE) is ApprovalLevel.APPROVE_ACTION


class TestSensitiveToolGate:
    async def test_unapproved_sensitive_tool_denied_fail_closed(self) -> None:
        tool = SendEmailTool()
        gate = SensitiveToolGate(sensitive_tools=frozenset({"send_email"}))
        registry = ToolRegistry(middlewares=[gate])
        registry.register(tool)
        task_id = uuid4()

        result = await registry.execute(
            InvocationContext(tool_name="send_email", arguments={}, task_id=task_id)
        )

        assert not result.success
        assert result.error is not None and "requires human approval" in result.error
        assert tool.sent == 0  # the tool NEVER ran

    async def test_allowance_lets_it_through(self) -> None:
        tool = SendEmailTool()
        task_id = uuid4()
        gate = SensitiveToolGate(
            sensitive_tools=frozenset({"send_email"}),
            allowed=frozenset({(str(task_id), "send_email")}),
        )
        registry = ToolRegistry(middlewares=[gate])
        registry.register(tool)

        result = await registry.execute(
            InvocationContext(tool_name="send_email", arguments={}, task_id=task_id)
        )

        assert result.success
        assert tool.sent == 1

    async def test_allowance_is_per_task(self) -> None:
        tool = SendEmailTool()
        gate = SensitiveToolGate(
            sensitive_tools=frozenset({"send_email"}),
            allowed=frozenset({(str(uuid4()), "send_email")}),  # some OTHER task
        )
        registry = ToolRegistry(middlewares=[gate])
        registry.register(tool)

        result = await registry.execute(
            InvocationContext(tool_name="send_email", arguments={}, task_id=uuid4())
        )

        assert not result.success
        assert tool.sent == 0

    async def test_non_sensitive_tools_unaffected(self) -> None:
        class PlainTool(Tool[_Args]):
            name = "plain"
            description = "Not sensitive."
            args_model = _Args

            async def execute(self, args: _Args) -> str:
                return "ok"

        gate = SensitiveToolGate(sensitive_tools=frozenset({"send_email"}))
        registry = ToolRegistry(middlewares=[gate])
        registry.register(PlainTool())

        result = await registry.execute(
            InvocationContext(tool_name="plain", arguments={}, task_id=uuid4())
        )

        assert result.success
