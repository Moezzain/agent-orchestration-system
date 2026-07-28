"""Trace endpoints — seeded spans + llm_calls rows against real Postgres."""

from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from asgi_lifespan import LifespanManager
from httpx import ASGITransport, AsyncClient

from orchestrator.api.main import create_app
from orchestrator.config import Settings
from orchestrator.db import LLMCallRepository, create_engine, create_session_factory
from orchestrator.db.models import SpanRow
from orchestrator.llm.types import LLMResponse, Usage
from orchestrator.schemas import AgentRole


@pytest.fixture
async def client() -> AsyncIterator[AsyncClient]:
    settings = Settings(_env_file=None)  # type: ignore[call-arg]
    engine = create_engine(settings)
    try:
        conn = await engine.connect()
        await conn.close()
    except OSError:
        pytest.skip("postgres not reachable — run `make infra-up`")
    finally:
        await engine.dispose()

    app = create_app()
    async with (
        LifespanManager(app),
        AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as http,
    ):
        yield http


async def _seed(task_id: UUID) -> None:
    settings = Settings(_env_file=None)  # type: ignore[call-arg]
    engine = create_engine(settings)
    factory = create_session_factory(engine)
    now = datetime.now(UTC)
    root_id, child_id = uuid4(), uuid4()
    async with factory() as session:
        session.add(
            SpanRow(
                id=root_id,
                task_id=task_id,
                parent_id=None,
                name="graph.work",
                kind="graph_node",
                status="unset",
                attributes={},
                started_at=now,
                ended_at=now + timedelta(milliseconds=1500),
            )
        )
        session.add(
            SpanRow(
                id=child_id,
                task_id=task_id,
                parent_id=root_id,
                name="llm.complete",
                kind="llm_call",
                status="unset",
                attributes={"llm.model": "claude-haiku-4-5", "llm.input_tokens": 900},
                started_at=now + timedelta(milliseconds=100),
                ended_at=now + timedelta(milliseconds=900),
            )
        )
        repo = LLMCallRepository(session)
        await repo.record(
            LLMResponse(
                text="x",
                usage=Usage(input_tokens=900, output_tokens=150),
                model="claude-haiku-4-5",
                provider="anthropic",
                stop_reason="end_turn",
                latency_ms=800,
            ),
            role=AgentRole.SPECIALIST,
            task_id=task_id,
        )
        await session.commit()
    await engine.dispose()


async def test_spans_and_costs_endpoints(client: AsyncClient) -> None:
    created = (await client.post("/tasks", json={"request": "trace endpoints"})).json()
    task_id = UUID(created["id"])
    await _seed(task_id)

    spans = (await client.get(f"/tasks/{task_id}/spans")).json()
    assert len(spans) == 2
    root = next(s for s in spans if s["parent_id"] is None)
    child = next(s for s in spans if s["parent_id"] is not None)
    assert root["name"] == "graph.work"
    assert root["duration_ms"] == 1500
    assert child["parent_id"] == root["id"]  # tree linkage survives the API
    assert child["attributes"]["llm.model"] == "claude-haiku-4-5"

    costs = (await client.get(f"/tasks/{task_id}/costs")).json()
    assert costs["total_usd"] > 0
    assert len(costs["rows"]) == 1
    row = costs["rows"][0]
    assert row["agent_role"] == "specialist"
    assert row["model"] == "claude-haiku-4-5"
    assert row["calls"] == 1
    assert row["input_tokens"] == 900


async def test_empty_task_returns_empty_collections(client: AsyncClient) -> None:
    created = (await client.post("/tasks", json={"request": "empty trace"})).json()

    spans = (await client.get(f"/tasks/{created['id']}/spans")).json()
    costs = (await client.get(f"/tasks/{created['id']}/costs")).json()

    assert spans == []
    assert costs == {"total_usd": 0.0, "rows": []}
