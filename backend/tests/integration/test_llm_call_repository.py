from sqlalchemy.ext.asyncio import AsyncSession

from orchestrator.db import LLMCallRepository, TaskRepository
from orchestrator.llm.types import LLMResponse, Usage
from orchestrator.schemas import AgentRole, Task


def _response(model: str, input_tokens: int, output_tokens: int) -> LLMResponse:
    return LLMResponse(
        text="output",
        usage=Usage(input_tokens=input_tokens, output_tokens=output_tokens),
        model=model,
        provider="anthropic",
        stop_reason="end_turn",
        latency_ms=150,
    )


async def test_record_and_aggregate_cost_per_task(db_session: AsyncSession) -> None:
    task = await TaskRepository(db_session).create(Task(request="research X"))
    repo = LLMCallRepository(db_session)

    # supervisor call on sonnet + two specialist calls on haiku
    c1 = await repo.record(
        _response("claude-sonnet-5", 10_000, 2_000),
        role=AgentRole.SUPERVISOR,
        task_id=task.id,
    )
    c2 = await repo.record(
        _response("claude-haiku-4-5", 5_000, 1_000),
        role=AgentRole.SPECIALIST,
        task_id=task.id,
    )
    c3 = await repo.record(
        _response("claude-haiku-4-5", 4_000, 800),
        role=AgentRole.SPECIALIST,
        task_id=task.id,
    )

    total = await repo.total_cost_for_task(task.id)

    assert total > 0
    assert abs(total - (c1 + c2 + c3)) < 1e-9


async def test_call_without_task_is_still_recorded(db_session: AsyncSession) -> None:
    repo = LLMCallRepository(db_session)

    cost = await repo.record(_response("claude-haiku-4-5", 1_000, 100), role=AgentRole.SPECIALIST)

    assert cost > 0
