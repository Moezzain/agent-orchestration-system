"""Working memory tests — need live Redis (make infra-up)."""

from collections.abc import AsyncIterator
from uuid import uuid4

import pytest

from orchestrator.config import Settings
from orchestrator.memory import WorkingMemory, create_working_memory_client
from orchestrator.schemas import AgentResult, Plan, SpecialistType, SubTask


@pytest.fixture
async def wm() -> AsyncIterator[WorkingMemory]:
    settings = Settings(_env_file=None)  # type: ignore[call-arg]
    client = create_working_memory_client(settings)
    try:
        await client.ping()
    except Exception:
        pytest.skip("redis not reachable — run `make infra-up`")
    memory = WorkingMemory(client, uuid4())
    yield memory
    await memory.clear()
    await client.aclose()


def _plan() -> Plan:
    return Plan(
        reasoning="research then write",
        subtasks=[
            SubTask(
                id="s1",
                description="research X",
                specialist=SpecialistType.RESEARCH,
                expected_output="notes",
                complexity=2,
            ),
            SubTask(
                id="s2",
                description="write summary",
                specialist=SpecialistType.WRITER,
                depends_on=["s1"],
                expected_output="markdown",
                complexity=2,
            ),
        ],
    )


def _result(subtask_id: str) -> AgentResult:
    return AgentResult(
        subtask_id=subtask_id,
        specialist=SpecialistType.RESEARCH,
        output=f"output of {subtask_id}",
        confidence=0.9,
    )


async def test_plan_roundtrips_typed(wm: WorkingMemory) -> None:
    await wm.save_plan(_plan())

    loaded = await wm.load_plan()

    assert loaded is not None
    assert loaded.subtasks[1].depends_on == ["s1"]
    assert isinstance(loaded, Plan)  # typed on the way out, not raw JSON


async def test_missing_plan_is_none(wm: WorkingMemory) -> None:
    assert await wm.load_plan() is None


async def test_results_accumulate_by_subtask(wm: WorkingMemory) -> None:
    await wm.save_result(_result("s1"))
    await wm.save_result(_result("s2"))

    all_results = await wm.load_results()
    one = await wm.load_result("s1")

    assert set(all_results) == {"s1", "s2"}
    assert one is not None and one.output == "output of s1"


async def test_errors_preserve_order(wm: WorkingMemory) -> None:
    await wm.append_error("first failure")
    await wm.append_error("second failure")

    assert await wm.errors() == ["first failure", "second failure"]


async def test_clear_removes_everything(wm: WorkingMemory) -> None:
    await wm.save_plan(_plan())
    await wm.save_result(_result("s1"))
    await wm.append_error("x")

    await wm.clear()

    assert await wm.load_plan() is None
    assert await wm.load_results() == {}
    assert await wm.errors() == []


async def test_tasks_are_isolated(wm: WorkingMemory) -> None:
    settings = Settings(_env_file=None)  # type: ignore[call-arg]
    client = create_working_memory_client(settings)
    other = WorkingMemory(client, uuid4())

    await wm.save_result(_result("s1"))

    assert await other.load_results() == {}
    await client.aclose()
