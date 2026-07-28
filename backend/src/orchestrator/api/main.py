"""FastAPI application factory.

Run locally:
    make api        (uvicorn on :8000; docs at /docs)
"""

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from orchestrator.api.deps import lifespan
from orchestrator.api.routes import approvals, memory, stream, tasks, traces


def create_app() -> FastAPI:
    app = FastAPI(
        title="Agent Orchestration API",
        version="0.1.0",
        lifespan=lifespan,
    )
    # dev CORS for the Vite frontend; tighten per-deployment
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://localhost:5173"],
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.include_router(tasks.router)
    app.include_router(stream.router)
    app.include_router(traces.router)
    app.include_router(approvals.router)
    app.include_router(memory.router)

    @app.get("/health", tags=["meta"])
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    return app


app = create_app()
