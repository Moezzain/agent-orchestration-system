"""Build the production GraphDeps + memory plumbing for one task run.

Everything async-client-shaped is constructed INSIDE the running event loop
(i.e., called from within asyncio.run), never at module import — the
loop-binding rule that keeps Celery and async clients from fighting.
"""

from dataclasses import dataclass
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from orchestrator.agents import build_reviewer, build_specialist, build_supervisor
from orchestrator.config import Settings
from orchestrator.db import LLMCallRepository, create_engine, create_session_factory
from orchestrator.graph.nodes import GraphDeps
from orchestrator.llm import ModelRouter, build_providers
from orchestrator.llm.types import LLMResponse
from orchestrator.memory import (
    LongTermMemory,
    OpenAIEmbedder,
    WorkingMemory,
    create_chroma_client,
    create_working_memory_client,
)
from orchestrator.memory.lifecycle import MemoryConsolidator
from orchestrator.observability.events import EventPublisher
from orchestrator.schemas import AgentResult, AgentRole, SubTask, Task
from orchestrator.tools.factory import build_default_registry


@dataclass
class RuntimeBundle:
    """Everything one task execution needs, plus cleanup handles."""

    deps: GraphDeps
    engine: AsyncEngine
    session_factory: async_sessionmaker[AsyncSession]
    working_memory_factory: object  # Callable[[UUID], WorkingMemory]
    consolidator: MemoryConsolidator | None
    publisher: EventPublisher | None = None

    async def dispose(self) -> None:
        await self.engine.dispose()


async def build_runtime_bundle(settings: Settings) -> RuntimeBundle:
    providers = build_providers(settings)
    if not providers:
        raise RuntimeError("no LLM API keys configured")

    router = ModelRouter(settings, providers)
    registry = build_default_registry(settings)
    engine = create_engine(settings)
    session_factory = create_session_factory(engine)

    # Long-term memory + consolidation need Chroma AND an embedder (OpenAI key);
    # degrade gracefully without them.
    memory: LongTermMemory | None = None
    consolidator: MemoryConsolidator | None = None
    if settings.openai_api_key is not None:
        try:
            chroma = await create_chroma_client(settings)
            await chroma.heartbeat()
            embedder = OpenAIEmbedder(settings.openai_api_key.get_secret_value())
            memory = LongTermMemory(chroma, embedder)
            cheap = router.resolve(AgentRole.SPECIALIST)
            consolidator = MemoryConsolidator(cheap.provider, cheap.model, memory)
        except Exception:
            memory = None
            consolidator = None

    async def run_specialist(subtask: SubTask, *, context: str, task: Task) -> AgentResult:
        async def record_cost(response: LLMResponse) -> None:
            async with session_factory() as session:
                await LLMCallRepository(session).record(
                    response, role=AgentRole.SPECIALIST, task_id=task.id
                )
                await session.commit()

        runtime = build_specialist(
            subtask.specialist, router=router, registry=registry, on_llm_response=record_cost
        )
        prompt = f"{subtask.description}\n\nExpected output: {subtask.expected_output}"
        return await runtime.run(prompt, subtask_id=subtask.id, context=context, task_id=task.id)

    wm_client = create_working_memory_client(settings)
    publisher = EventPublisher(wm_client)

    deps = GraphDeps(
        supervisor=build_supervisor(router, memory=memory),
        reviewer=build_reviewer(router),
        run_specialist=run_specialist,
        emit=publisher.publish,
    )

    def working_memory_factory(task_id: UUID) -> WorkingMemory:
        return WorkingMemory(wm_client, task_id)

    return RuntimeBundle(
        deps=deps,
        engine=engine,
        session_factory=session_factory,
        working_memory_factory=working_memory_factory,
        consolidator=consolidator,
        publisher=publisher,
    )
