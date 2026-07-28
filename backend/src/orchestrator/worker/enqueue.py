"""Manual enqueue helper (the API does this properly in step 29).

Usage:
    uv run python -m orchestrator.worker.enqueue "research X and summarize"
Then watch it run in a worker started with `make worker`.
"""

import asyncio
import sys

from orchestrator.config import get_settings
from orchestrator.db import TaskRepository, create_engine, create_session_factory
from orchestrator.schemas import Task
from orchestrator.worker.tasks import run_graph_task


async def main() -> None:
    request = sys.argv[1] if len(sys.argv) > 1 else sys.exit("usage: enqueue '<task request>'")
    settings = get_settings()
    engine = create_engine(settings)
    factory = create_session_factory(engine)

    task = Task(request=request)
    async with factory() as session:
        await TaskRepository(session).create(task)
        await session.commit()
    await engine.dispose()

    run_graph_task.delay(str(task.id))
    print(f"enqueued task {task.id}")


if __name__ == "__main__":
    asyncio.run(main())
