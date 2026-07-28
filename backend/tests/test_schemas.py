import pytest
from pydantic import ValidationError

from orchestrator.schemas import (
    AgentResult,
    Plan,
    SpecialistType,
    SubTask,
    SubTaskStatus,
    Task,
    TaskStatus,
)


def _subtask(sid: str, depends_on: list[str] | None = None) -> SubTask:
    return SubTask(
        id=sid,
        description=f"do {sid}",
        specialist=SpecialistType.RESEARCH,
        depends_on=depends_on or [],
        expected_output="markdown summary",
        complexity=2,
    )


class TestPlanValidation:
    def test_valid_plan_accepted(self) -> None:
        plan = Plan(
            reasoning="split into research then writing",
            subtasks=[_subtask("s1"), _subtask("s2", depends_on=["s1"])],
        )
        assert len(plan.subtasks) == 2

    def test_unknown_dependency_rejected(self) -> None:
        with pytest.raises(ValidationError, match="unknown ids"):
            Plan(reasoning="r", subtasks=[_subtask("s1", depends_on=["s99"])])

    def test_duplicate_ids_rejected(self) -> None:
        with pytest.raises(ValidationError, match="unique"):
            Plan(reasoning="r", subtasks=[_subtask("s1"), _subtask("s1")])

    def test_self_dependency_rejected(self) -> None:
        with pytest.raises(ValidationError, match="depends on itself"):
            Plan(reasoning="r", subtasks=[_subtask("s1", depends_on=["s1"])])

    def test_cycle_rejected(self) -> None:
        with pytest.raises(ValidationError, match="cycle"):
            Plan(
                reasoning="r",
                subtasks=[
                    _subtask("s1", depends_on=["s2"]),
                    _subtask("s2", depends_on=["s1"]),
                ],
            )

    def test_llm_style_id_pattern_enforced(self) -> None:
        with pytest.raises(ValidationError):
            _subtask("step-one")


class TestExecutionBatches:
    def test_batches_respect_dependencies(self) -> None:
        plan = Plan(
            reasoning="diamond: s2 and s3 depend on s1; s4 needs both",
            subtasks=[
                _subtask("s1"),
                _subtask("s2", depends_on=["s1"]),
                _subtask("s3", depends_on=["s1"]),
                _subtask("s4", depends_on=["s2", "s3"]),
            ],
        )

        batches = [[s.id for s in batch] for batch in plan.execution_batches()]

        assert batches == [["s1"], ["s2", "s3"], ["s4"]]


class TestImmutability:
    def test_mutation_raises(self) -> None:
        task = Task(request="research X")
        with pytest.raises(ValidationError):
            task.status = TaskStatus.RUNNING

    def test_model_copy_produces_updated_instance(self) -> None:
        task = Task(request="research X")

        running = task.model_copy(update={"status": TaskStatus.RUNNING})

        assert task.status == TaskStatus.PENDING
        assert running.status == TaskStatus.RUNNING
        assert running.id == task.id


class TestSerialization:
    def test_enums_serialize_to_strings(self) -> None:
        sub = _subtask("s1")

        dumped = sub.model_dump(mode="json")

        assert dumped["specialist"] == "research"
        assert dumped["status"] == SubTaskStatus.PENDING.value

    def test_confidence_bounds_enforced(self) -> None:
        with pytest.raises(ValidationError):
            AgentResult(
                subtask_id="s1",
                specialist=SpecialistType.WRITER,
                output="text",
                confidence=1.5,
            )

    def test_plan_json_schema_exports_for_llm(self) -> None:
        schema = Plan.model_json_schema()

        assert "subtasks" in schema["properties"]
        # descriptions become prompt material for structured output
        subtask_props = schema["$defs"]["SubTask"]["properties"]
        assert "description" in subtask_props["depends_on"]
