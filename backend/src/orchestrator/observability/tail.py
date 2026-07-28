"""Live-tail a task's event stream.

Usage:
    uv run python -m orchestrator.observability.tail <task_id>
Subscribe BEFORE enqueueing (pub/sub has no replay) or you'll miss the start.
"""

import asyncio
import sys
from uuid import UUID

from orchestrator.config import get_settings
from orchestrator.memory import create_working_memory_client
from orchestrator.observability.events import subscribe_events


async def main() -> None:
    if len(sys.argv) < 2:
        sys.exit("usage: tail <task_id>")
    task_id = UUID(sys.argv[1])

    client = create_working_memory_client(get_settings())
    print(f"tailing task {task_id} (ctrl-c to stop)...")
    try:
        async for event in subscribe_events(client, task_id):
            print(f"[{event.ts}] {event.type.value}: {event.data}")
    finally:
        await client.aclose()


if __name__ == "__main__":
    asyncio.run(main())
