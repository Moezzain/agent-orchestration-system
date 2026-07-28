"""Tracing tests: instrumentation via InMemorySpanExporter (no infra),
exporter row-mapping as a pure function.

Note: OTel's global tracer provider can only be set once per process, so
these tests install a private TracerProvider and pass tracers... we instead
exercise instrumentation through the global-API path by setting the provider
here (first-wins). If the suite ever needs isolation, OTel test utilities
provide tracer_provider injection points.
"""

from typing import Any
from uuid import uuid4

from langchain_core.runnables import RunnableConfig
from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from orchestrator.graph.build import build_graph
from orchestrator.graph.nodes import GraphDeps
from orchestrator.graph.state import initial_state
from orchestrator.observability.otel import KIND_ATTR, TASK_ID_ATTR, _row
from orchestrator.schemas import Task
from tests.test_graph import FakeSpecialistRunner, FakeSupervisor, _accept_all, _chain_plan

# first-wins global provider for the whole test process
_exporter = InMemorySpanExporter()
_provider = TracerProvider()
_provider.add_span_processor(SimpleSpanProcessor(_exporter))
trace.set_tracer_provider(_provider)


async def test_graph_run_emits_node_spans_with_task_id() -> None:
    _exporter.clear()
    runner = FakeSpecialistRunner()
    deps = GraphDeps(
        supervisor=FakeSupervisor(_chain_plan()),
        reviewer=_accept_all("s1", "s2"),
        run_specialist=runner,
    )
    graph = build_graph(deps)
    task = Task(request="tracing test")
    config: RunnableConfig = {"configurable": {"thread_id": str(uuid4())}}

    final: dict[str, Any] = await graph.ainvoke(initial_state(task), config)
    assert final["final_output"] is not None

    spans = _exporter.get_finished_spans()
    names = [s.name for s in spans]
    # both work units + the fixed pipeline nodes appear
    assert names.count("graph.work") == 2
    for expected in ("graph.supervise", "graph.dispatch", "graph.synthesize"):
        assert expected in names

    supervise = next(s for s in spans if s.name == "graph.supervise")
    assert supervise.attributes is not None
    assert supervise.attributes[KIND_ATTR] == "graph_node"
    assert supervise.attributes[TASK_ID_ATTR] == str(task.id)


async def test_work_span_present_for_send_payloads_too() -> None:
    # work nodes receive WorkItem dicts (not full state) — task attr must still land
    _exporter.clear()
    deps = GraphDeps(
        supervisor=FakeSupervisor(_chain_plan()),
        reviewer=_accept_all("s1", "s2"),
        run_specialist=FakeSpecialistRunner(),
    )
    graph = build_graph(deps)
    task = Task(request="work span test")
    config: RunnableConfig = {"configurable": {"thread_id": str(uuid4())}}
    await graph.ainvoke(initial_state(task), config)

    work_spans = [s for s in _exporter.get_finished_spans() if s.name == "graph.work"]
    assert all(
        s.attributes is not None and s.attributes.get(TASK_ID_ATTR) == str(task.id)
        for s in work_spans
    )


def test_row_mapping_extracts_ids_kind_and_attrs() -> None:
    _exporter.clear()
    tracer = trace.get_tracer("test")
    task_id = uuid4()
    with tracer.start_as_current_span("parent") as parent_span:
        parent_span.set_attribute(TASK_ID_ATTR, str(task_id))
        parent_span.set_attribute(KIND_ATTR, "task")
        with tracer.start_as_current_span("llm.complete") as child:
            child.set_attribute(KIND_ATTR, "llm_call")
            child.set_attribute(TASK_ID_ATTR, str(task_id))
            child.set_attribute("llm.model", "claude-haiku-4-5")
            child.set_attribute("llm.input_tokens", 123)

    spans = {s.name: s for s in _exporter.get_finished_spans()}
    parent_row = _row(spans["parent"])
    child_row = _row(spans["llm.complete"])
    assert parent_row is not None and child_row is not None

    p_id, p_task, p_parent, _, p_kind, _, _, _, _ = parent_row
    c_id, c_task, c_parent, c_name, c_kind, _, c_attrs, c_start, c_end = child_row

    assert p_task == str(task_id) and c_task == str(task_id)
    assert p_parent is None
    assert c_parent == p_id  # tree linkage via deterministic UUIDs
    assert c_kind == "llm_call" and p_kind == "task"
    assert c_name == "llm.complete"
    assert '"llm.model": "claude-haiku-4-5"' in c_attrs
    assert '"llm.input_tokens": 123' in c_attrs
    # our marker attrs are extracted into columns, not duplicated in the blob
    assert TASK_ID_ATTR not in c_attrs
    assert c_end >= c_start
