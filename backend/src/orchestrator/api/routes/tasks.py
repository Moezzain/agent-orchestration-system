"""Task endpoints: create (enqueue), read, list."""

from uuid import UUID

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from orchestrator.api.deps import SessionDep
from orchestrator.db import TaskRepository
from orchestrator.schemas import Task, TaskStatus

router = APIRouter(prefix="/tasks", tags=["tasks"])


class CreateTaskRequest(BaseModel):
    request: str = Field(min_length=1, max_length=10_000)
    require_review: bool = False


class TaskResponse(BaseModel):
    id: UUID
    request: str
    status: TaskStatus
    result: str | None
    require_review: bool

    @classmethod
    def from_domain(cls, task: Task) -> "TaskResponse":
        return cls(
            id=task.id,
            request=task.request,
            status=task.status,
            result=task.result,
            require_review=task.require_review,
        )


@router.post("", status_code=202, response_model=TaskResponse)
async def create_task(body: CreateTaskRequest, session: SessionDep) -> TaskResponse:
    """Create the row, enqueue the run, return immediately (202 Accepted)."""
    task = Task(request=body.request, require_review=body.require_review)
    created = await TaskRepository(session).create(task)
    await session.commit()  # row must exist before any worker can pick it up

    # import here: keeps celery out of module import (worker pulls this module's app)
    from orchestrator.worker.tasks import run_graph_task

    run_graph_task.delay(str(created.id))
    return TaskResponse.from_domain(created)


@router.get("/{task_id}", response_model=TaskResponse)
async def get_task(task_id: UUID, session: SessionDep) -> TaskResponse:
    task = await TaskRepository(session).get(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="task not found")
    return TaskResponse.from_domain(task)


@router.get("", response_model=list[TaskResponse])
async def list_tasks(session: SessionDep, limit: int = 50) -> list[TaskResponse]:
    tasks = await TaskRepository(session).list_recent(limit=min(limit, 200))
    return [TaskResponse.from_domain(t) for t in tasks]
