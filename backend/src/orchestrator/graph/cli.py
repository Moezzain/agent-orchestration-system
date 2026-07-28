"""End-to-end CLI run of the full graph with real providers and tools.

Usage:
    uv run python -m orchestrator.graph.cli "research X and summarize it"
Requires ANTHROPIC_API_KEY + OPENAI_API_KEY (+ TAVILY_API_KEY for web search).
"""

import asyncio
import sys
from pathlib import Path
from uuid import uuid4

from langchain_core.runnables import RunnableConfig

from orchestrator.agents import build_reviewer, build_specialist, build_supervisor
from orchestrator.config import get_settings
from orchestrator.graph.build import build_graph
from orchestrator.graph.nodes import GraphDeps
from orchestrator.graph.state import initial_state
from orchestrator.llm import ModelRouter, build_providers
from orchestrator.schemas import AgentResult, SubTask, Task
from orchestrator.tools import ToolRegistry
from orchestrator.tools.builtin import CodeExecutionTool, FileReadTool, FileWriteTool, WebSearchTool


def _build_registry() -> ToolRegistry:
    settings = get_settings()
    registry = ToolRegistry()
    workspace = Path(settings.workspace_dir)
    registry.register(FileReadTool(workspace))
    registry.register(FileWriteTool(workspace))
    registry.register(CodeExecutionTool())
    if settings.tavily_api_key is not None:
        registry.register(WebSearchTool(settings.tavily_api_key.get_secret_value()))
    return registry


async def main() -> None:
    settings = get_settings()
    providers = build_providers(settings)
    if not providers:
        sys.exit("no API keys configured — fill in .env")

    router = ModelRouter(settings, providers)
    registry = _build_registry()

    async def run_specialist(subtask: SubTask, *, context: str, task: Task) -> AgentResult:
        runtime = build_specialist(subtask.specialist, router=router, registry=registry)
        prompt = f"{subtask.description}\n\nExpected output: {subtask.expected_output}"
        return await runtime.run(prompt, subtask_id=subtask.id, context=context, task_id=task.id)

    deps = GraphDeps(
        supervisor=build_supervisor(router),  # long-term memory wired in later steps
        reviewer=build_reviewer(router),
        run_specialist=run_specialist,
    )
    graph = build_graph(deps)

    request = sys.argv[1] if len(sys.argv) > 1 else "Explain what this system is in one paragraph."
    task = Task(request=request)
    config: RunnableConfig = {"configurable": {"thread_id": str(uuid4())}}

    print(f"task: {task.request}\n{'=' * 60}")
    async for update in graph.astream(initial_state(task), config, stream_mode="updates"):
        for node, payload in update.items():
            if node == "supervise" and payload.get("plan"):
                print("\n[supervise] plan:")
                for s in payload["plan"].subtasks:
                    deps_str = f" <- {s.depends_on}" if s.depends_on else ""
                    print(f"  {s.id} [{s.specialist.value}] {s.description[:80]}{deps_str}")
            elif node == "dispatch":
                ready = payload.get("ready_ids") or []
                print(f"\n[dispatch] -> {', '.join(ready) if ready else 'done, synthesizing'}")
            elif node == "work":
                for sid, r in payload.get("results", {}).items():
                    rv = payload.get("reviews", {}).get(sid)
                    verdict = rv.verdict.value if rv else "?"
                    print(
                        f"[work:{sid}] confidence={r.confidence:.2f} review={verdict} "
                        f"output={r.output[:80]!r}..."
                    )
            elif node == "synthesize":
                print(f"\n{'=' * 60}\nFINAL OUTPUT:\n{payload.get('final_output')}")


if __name__ == "__main__":
    asyncio.run(main())
