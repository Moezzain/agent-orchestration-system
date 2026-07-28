"""Durable checkpointing — needs live Postgres (make infra-up)."""

from typing import Any
from uuid import uuid4

import pytest
from langchain_core.runnables import RunnableConfig

from orchestrator.config import Settings
from orchestrator.graph.build import build_graph
from orchestrator.graph.checkpointing import postgres_checkpointer, psycopg_dsn
from orchestrator.graph.nodes import GraphDeps
from orchestrator.graph.state import initial_state
from orchestrator.schemas import Task
from tests.test_graph import FakeSpecialistRunner, FakeSupervisor, _accept_all, _chain_plan


async def test_run_persists_checkpoints_and_state_is_recoverable() -> None:
    settings = Settings(_env_file=None)  # type: ignore[call-arg]
    try:
        checkpointer_cm = postgres_checkpointer(settings)
        saver = await checkpointer_cm.__aenter__()
    except Exception:
        pytest.skip("postgres not reachable — run `make infra-up`")

    try:
        deps = GraphDeps(
            supervisor=FakeSupervisor(_chain_plan()),
            reviewer=_accept_all("s1", "s2"),
            run_specialist=FakeSpecialistRunner(),
        )
        graph = build_graph(deps, checkpointer=saver)
        thread_id = str(uuid4())
        config: RunnableConfig = {"configurable": {"thread_id": thread_id}}

        final: dict[str, Any] = await graph.ainvoke(
            initial_state(Task(request="checkpointed run")), config
        )
        assert final["final_output"] is not None

        # a NEW graph instance over the same saver recovers the thread's state —
        # this is the durability that pause/resume (step 27) stands on
        graph2 = build_graph(deps, checkpointer=saver)
        snapshot = await graph2.aget_state(config)
        assert snapshot.values["final_output"] == final["final_output"]
        assert snapshot.values["subtask_status"] == {"s1": "accepted", "s2": "accepted"}

        # checkpoint history exists (one per superstep, newest first)
        history = [entry async for entry in graph2.aget_state_history(config)]
        assert len(history) >= 4  # supervise, dispatch, work, dispatch, synthesize...
    finally:
        await checkpointer_cm.__aexit__(None, None, None)


def test_dsn_conversion_strips_asyncpg_dialect() -> None:
    settings = Settings(_env_file=None)  # type: ignore[call-arg]
    dsn = psycopg_dsn(settings)
    assert dsn.startswith("postgresql://")
    assert "+asyncpg" not in dsn
