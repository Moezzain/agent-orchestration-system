"""Assemble and compile the orchestration graph."""

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph

from orchestrator.graph.nodes import GraphDeps, make_nodes, route_after_dispatch
from orchestrator.graph.state import OrchestratorState


def build_graph(
    deps: GraphDeps,
    *,
    checkpointer: BaseCheckpointSaver | None = None,  # type: ignore[type-arg]
) -> CompiledStateGraph:  # type: ignore[type-arg]
    nodes = make_nodes(deps)

    graph: StateGraph = StateGraph(OrchestratorState)  # type: ignore[type-arg]
    for name, fn in nodes.items():
        graph.add_node(name, fn)

    graph.add_edge(START, "supervise")
    # plan_gate: interrupt-isolated plan approval (no-op when policy is quiet)
    graph.add_edge("supervise", "plan_gate")
    graph.add_edge("plan_gate", "dispatch")
    # dispatch either fans out Send packets to work, or moves to synthesize
    graph.add_conditional_edges("dispatch", route_after_dispatch, ["work", "synthesize"])
    # all work nodes join at the escalation gate; parked subtasks get their
    # human decisions there before the next dispatch round
    graph.add_edge("work", "escalation_gate")
    graph.add_edge("escalation_gate", "dispatch")
    graph.add_edge("synthesize", END)

    return graph.compile(checkpointer=checkpointer or InMemorySaver())
