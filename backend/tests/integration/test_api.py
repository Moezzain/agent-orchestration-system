"""API tests: in-process ASGI (no server), real Postgres, celery stubbed.

httpx's ASGITransport drives the FastAPI app directly — full request cycle
including lifespan, DI, and serialization, no socket.
"""

from collections.abc import AsyncIterator
from typing import Any
from uuid import UUID, uuid4

import pytest
from asgi_lifespan import LifespanManager
from httpx import ASGITransport, AsyncClient

from orchestrator.api.main import create_app
from orchestrator.config import Settings
from orchestrator.db import ApprovalRepository, create_engine, create_session_factory
from orchestrator.schemas import ApprovalLevel, Escalation, EscalationReason


@pytest.fixture
async def client(monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[AsyncClient]:
    settings = Settings(_env_file=None)  # type: ignore[call-arg]
    engine = create_engine(settings)
    try:
        conn = await engine.connect()
        await conn.close()
    except OSError:
        pytest.skip("postgres not reachable — run `make infra-up`")
    finally:
        await engine.dispose()

    # stub celery enqueues: record instead of dispatch
    enqueued: list[tuple[str, tuple[str, ...]]] = []

    class _FakeSig:
        def __init__(self, name: str) -> None:
            self._name = name

        def delay(self, *args: str) -> None:
            enqueued.append((self._name, args))

    import orchestrator.worker.tasks as worker_tasks

    monkeypatch.setattr(worker_tasks, "run_graph_task", _FakeSig("run"))
    monkeypatch.setattr(worker_tasks, "resume_graph_task", _FakeSig("resume"))

    app = create_app()
    async with (
        LifespanManager(app),
        AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as http,
    ):
        http.enqueued = enqueued  # type: ignore[attr-defined]
        yield http


async def test_create_task_persists_and_enqueues(client: AsyncClient) -> None:
    response = await client.post(
        "/tasks", json={"request": "api test task", "require_review": True}
    )

    assert response.status_code == 202
    body: dict[str, Any] = response.json()
    assert body["status"] == "pending"
    assert body["require_review"] is True

    enqueued = client.enqueued  # type: ignore[attr-defined]
    assert enqueued == [("run", (body["id"],))]

    fetched = await client.get(f"/tasks/{body['id']}")
    assert fetched.status_code == 200
    assert fetched.json()["request"] == "api test task"


async def test_validation_rejects_empty_request(client: AsyncClient) -> None:
    response = await client.post("/tasks", json={"request": ""})
    assert response.status_code == 422  # pydantic min_length at the boundary


async def test_missing_task_404(client: AsyncClient) -> None:
    response = await client.get(f"/tasks/{uuid4()}")
    assert response.status_code == 404


async def test_approval_flow_decide_enqueues_resume(client: AsyncClient) -> None:
    # seed: a task and a pending approval (as the worker would create them)
    created = (await client.post("/tasks", json={"request": "approval flow"})).json()
    task_id = UUID(created["id"])

    settings = Settings(_env_file=None)  # type: ignore[call-arg]
    engine = create_engine(settings)
    factory = create_session_factory(engine)
    escalation = Escalation(
        task_id=task_id,
        subtask_id="s1",
        reason=EscalationReason.LOW_REVIEW_SCORE,
        level=ApprovalLevel.APPROVE_ACTION,
        proposed_action="Accept or reject s1",
        context="score 0.2",
    )
    async with factory() as session:
        await ApprovalRepository(session).create_from_escalation(escalation)
        await session.commit()
    await engine.dispose()

    pending = (await client.get("/approvals", params={"task_id": str(task_id)})).json()
    assert len(pending) == 1
    assert pending[0]["reason"] == "low_review_score"

    decided = await client.post(
        f"/approvals/{escalation.id}/decision", json={"approved": True, "note": "ship it"}
    )
    assert decided.status_code == 200
    assert decided.json()["decision"] == "approved"

    enqueued = client.enqueued  # type: ignore[attr-defined]
    assert ("resume", (str(task_id), str(escalation.id))) in enqueued

    # idempotency: second decision on the same approval conflicts
    again = await client.post(f"/approvals/{escalation.id}/decision", json={"approved": False})
    assert again.status_code == 409
