# Agent Orchestration System

A production-shaped multi-agent orchestration platform: a supervisor LLM decomposes a task into a
dependency graph of subtasks, specialist agents execute them (in parallel where the graph allows),
a cross-provider reviewer scores every output, and an escalation policy pauses the run — durably,
mid-graph — whenever a human needs to look. Approve from the web console or CLI and the run resumes
from its checkpoint, without re-executing finished work.

**Stack:** Python 3.12 · LangGraph · FastAPI · Celery · PostgreSQL · Redis · ChromaDB · Docker ·
React (Vite) · OpenTelemetry · Anthropic + OpenAI

---

## Architecture

```
                          ┌─────────────────────────┐
                          │   React ops console      │
                          │  tasks · approvals ·     │
                          │  live SSE · trace/cost   │
                          └───────────┬─────────────┘
                                      │ HTTP + SSE
                          ┌───────────▼─────────────┐
                          │        FastAPI           │
                          │ POST /tasks (202)        │
                          │ POST /approvals/{id}/    │
                          │      decision            │
                          │ GET  /tasks/{id}/stream  │──── Redis pub/sub
                          │ GET  /tasks/{id}/spans   │     task:{id}:events
                          │ GET  /tasks/{id}/costs   │
                          └───────────┬─────────────┘
                                      │ celery .delay (Redis broker)
                          ┌───────────▼─────────────┐
                          │      Celery worker       │
                          │  one task = one graph    │
                          │  run/resume segment      │
                          └───────────┬─────────────┘
                                      │
              ┌───────────────────────▼───────────────────────────┐
              │              LangGraph state machine               │
              │                                                    │
              │  supervise ─► plan_gate ─► dispatch ─► work ×N     │
              │  (plan)      (interrupt)   (Send      (specialist  │
              │                            fan-out)    + review    │
              │                               ▲        + revise)   │
              │                               └──── escalation_gate│
              │                                     (interrupt)    │
              │                            synthesize ─► END       │
              │                                                    │
              │  checkpoints: AsyncPostgresSaver (durable pause)   │
              └──────┬──────────────┬──────────────┬──────────────┘
                     │              │              │
        ┌────────────▼──┐   ┌───────▼───────┐  ┌───▼────────────────┐
        │  LLM layer     │   │ Tool registry │  │  Memory            │
        │ provider-      │   │ middleware    │  │  working: Redis    │
        │ neutral        │   │ chain, rate   │  │  (task-scoped,TTL) │
        │ Anthropic +    │   │ limits, gate  │  │  long-term: Chroma │
        │ OpenAI adapters│   │ + MCP adapter │  │  (importance-      │
        │ role→model     │   │ + Docker      │  │   weighted recall) │
        │ router         │   │   sandbox     │  └────────────────────┘
        └────────────────┘   └───────────────┘
                     │
        ┌────────────▼───────────────────────────────┐
        │ Observability: OTel spans → Postgres,       │
        │ per-call cost tracking, event bus → SSE     │
        └─────────────────────────────────────────────┘
```

**The signature flow (golden path):** submit → supervisor plans → specialists work →
review escalates → graph `interrupt()` inside a pure gate node → checkpoint persisted →
Celery task *ends* → approval row awaits a human → decision recorded → new Celery task
resumes with `Command(resume=...)` → graph continues from the checkpoint → completed.
Nothing re-executes, nothing is held in RAM while waiting — a human can take a week to decide.

## What it demonstrates

- **Multi-agent orchestration** — supervisor / specialist / reviewer roles, dependency-ordered
  execution with true parallel fan-out (LangGraph `Send` API + merge reducers).
- **Durable human-in-the-loop** — `interrupt()` isolated in side-effect-free gate nodes (the
  LangGraph re-execution footgun, solved architecturally and pinned by tests), Postgres
  checkpointing, resume across process boundaries.
- **Escalation policy as pure functions** — objective signals (review score, confidence,
  sensitive tools, explicit request) mapped to levels: notify / approve_action / approve_plan.
- **Provider-neutral LLM layer** — hand-rolled Anthropic + OpenAI adapters behind a `Protocol`,
  role→model routing (supervisor: Sonnet, specialists: Haiku, reviewer: GPT-5 for
  cross-provider decorrelation), structured output with validation-retry.
- **Safe tool use** — validated `Tool[ArgsT]` contract, errors-are-data, Express-style middleware
  (observability, Redis token-bucket rate limiting, fail-closed sensitive-tool gate), MCP server
  adapter, and a hardened Docker code-execution sandbox (`--network=none`, read-only rootfs,
  noexec tmpfs, nobody user, pid/mem/cpu limits).
- **Two-tier memory** — Redis task-scoped working memory (TTL'd) and Chroma long-term memory with
  importance-weighted recall and LLM-driven consolidation at task end.
- **Observability** — OpenTelemetry spans (graph nodes, LLM calls, tools) exported to Postgres,
  per-call cost attribution, Redis pub/sub event bus streamed to the browser over SSE.

## Quickstart (Docker)

```bash
cp .env.example .env        # add ANTHROPIC_API_KEY and/or OPENAI_API_KEY (+ TAVILY_API_KEY for web search)
docker compose up -d --build
```

| Service  | URL                          |
|----------|------------------------------|
| Frontend | http://localhost:5173        |
| API      | http://localhost:8000/docs   |

The api container runs Alembic migrations on boot. The worker mounts the host Docker socket so
the code-execution sandbox can spawn sibling containers (documented tradeoff — see
[docs/DECISIONS.md](docs/DECISIONS.md)).

## Demo

Interactive golden-path walkthrough (submit → pause → approve → complete) against the running stack:

```bash
./scripts/demo.sh
```

Or click through it: open the frontend, submit a task with **Require plan review** checked, watch
live events stream in, approve from the amber queue card, watch it resume and finish — then open
the trace panel for the span tree and cost breakdown.

## Development (host)

```bash
make infra-up      # postgres + redis + chroma only
make install       # uv sync (backend) + npm install (frontend)
make api           # uvicorn with reload
make worker        # celery worker (solo pool — see DECISIONS)
make frontend      # vite dev server
make check         # ruff + mypy --strict + pytest (unit + integration)
```

170 backend tests; integration tests hit real Postgres/Redis and skip cleanly when infra is down.
The golden path has a dedicated worker-level test:
[`backend/tests/integration/test_golden_path.py`](backend/tests/integration/test_golden_path.py).

`LLM_CHEAP_MODE=1` routes every role to the cheapest model for inexpensive dev loops.

## Repo layout

```
backend/src/orchestrator/
  llm/            provider protocol, anthropic/openai adapters, router, pricing
  tools/          registry, middleware, builtins, docker sandbox, MCP adapter
  memory/         redis working memory, chroma long-term, consolidation
  agents/         supervisor, specialist, reviewer
  graph/          langgraph nodes, state, checkpointing
  hitl/           escalation policy, sensitive-tool gate, approval CLI
  worker/         celery app, task segments, runtime bundle
  api/            fastapi routes, SSE stream
  observability/  event bus, otel setup, postgres span exporter
frontend/src/     react ops console (tasks, approvals, live stream, traces)
docker/           backend + frontend images, nginx, entrypoints
docs/             PLAN.md (build plan), DECISIONS.md (architecture decisions)
```

## Key decisions

Full rationale in [docs/DECISIONS.md](docs/DECISIONS.md). The headlines:

| Decision | Why |
|---|---|
| `interrupt()` only in pure gate nodes | LangGraph re-executes the interrupting node on resume; isolating interrupts makes re-execution free |
| Celery task ends at pause; resume is a new task | No worker slot held hostage while a human decides; survives restarts |
| Reviewer on a different provider than workers | Decorrelates failure modes — a model family tends to like its own outputs |
| Errors-are-data in tools | Tool failures return LLM-readable results so the agent can adapt, instead of exceptions killing the run |
| Celery solo pool | Async clients bind to one event loop; prefork forking breaks them. Scale = more containers |
| Hand-rolled provider adapters (no LiteLLM) | Two providers don't justify a dependency; normalization differences are the interview material |
| Spans to Postgres, not Jaeger/Tempo | One less service; the UI reads spans over the same API. Ceiling documented |
