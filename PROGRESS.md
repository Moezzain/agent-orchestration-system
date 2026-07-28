# PROGRESS

Living doc. Full plan: [docs/PLAN.md](docs/PLAN.md). Update every session.

## Status

**Current phase:** Phase 9 — Ship — **PROJECT COMPLETE** 🎉
**Last completed step:** 38 (final) — Golden-path integration test + README + DECISIONS.md + demo script. All 38 steps done; 170 backend tests green; live demo verified against the containerized stack (2026-09-24).
**Next step:** none — backlog items below are optional polish.

**Deferred/backlog:**
- DB-query + generic HTTP tools (step 13 TRIM items) — add only if demo scenario needs them.
- read_file offset/limit paging (currently truncates at 100K chars with marker).
- Memory access-frequency boost (BRD "frequently accessed memories are more important"): bump importance/hit-count on retrieval. Small; slot anywhere after step 16.
- Memory expiration sweep (plan step 16 CUT item): decay stale low-importance memories.

## Step Log

### 2026-09-24 — Step 38 (FINAL): Golden-path test + README + DECISIONS + demo ✅ — PROJECT COMPLETE
- `backend/tests/integration/test_golden_path.py`: the signature flow at worker level against real Postgres+Redis — `execute_task` → review escalates → `awaiting_approval` + approval row (reason `low_review_score`, only s1 ran) → `ApprovalRepository.decide(APPROVED)` → `resume_task` → COMPLETED, result persisted, s1 NOT re-executed (`runner.runs == ["s1","s2"]`), working memory cleared. 170 tests green, mypy --strict clean.
- `README.md`: architecture diagram (final shape: console → API/SSE → celery → graph with gates → llm/tools/memory → otel), golden-path narrative, quickstart (`cp .env.example .env && docker compose up -d --build`), dev targets, repo layout, key-decisions table.
- `docs/DECISIONS.md`: 17-entry decision log (choice / optimizes-for / trades-away) — the interview-defense doc: interrupt-in-pure-gates, pause-ends-celery-task, cross-provider reviewer, solo pool, errors-are-data, sandbox DooD tradeoff, spans-to-Postgres ceiling, etc.
- `scripts/demo.sh`: interactive golden path via curl+jq — submit with `require_review`, wait for pause, show escalation context, approve, wait, print result + cost breakdown.
- Live demo run verified: plan gate paused (`explicit_request`/`approve_plan`, plan context included memory recall), approve → resume → "The result of 17 multiplied by 23 is 391.", $0.0088 across 6 haiku calls. En route: purged another stale celery backlog (8 queued tasks blocking the solo worker; Tavily key 401s in old runs).
- Known flake: `test_api_stream.py::test_stream_delivers_events_and_closes_on_terminal` occasionally loses the pub/sub race under full-suite timing; passes standalone/rerun.

### 2026-09-24 — Step 37: Full dockerization ✅ — CONTAINERIZED E2E RUN VERIFIED
- `docker/backend.Dockerfile`: multi-stage uv (deps layer cached separately), python:3.12-slim runtime, non-root user, docker CLI copied from docker:cli image (sandbox DooD), README copy fix (uv build needs it). Shared by api+worker.
- `docker/frontend.Dockerfile`: node build → nginx (SPA fallback, gzip); VITE_API_URL build arg (browser-reachable, not compose hostname).
- compose: api (entrypoint runs alembic upgrade then uvicorn; python-urllib healthcheck), worker (solo pool; /var/run/docker.sock mounted + root for sandbox — documented tradeoff; depends_on api healthy ⇒ migrations applied), frontend (:5173→80), shared workspace volume, anchored backend env (service hostnames), .dockerignore.
- Fixed en route: darwin rolldown binding → optionalDependencies (broke linux build); **pricing bug** — providers return dated model ids (claude-haiku-4-5-20251001) vs alias table → longest-prefix lookup + test (169 backend tests green); purged 10-task stale celery backlog pre-verification.
- Verified: `docker compose up -d --build` → health ok, frontend 200, fresh task → completed "The result of 17*23 is 391." with sandbox execution inside the container and cost endpoint showing $0.0060 correctly attributed.
- NOT committed.

### 2026-09-24 — Step 36: Trace explorer ✅ (Phase 8 complete)
- Backend: `SpanRepository.list_for_task`, `LLMCallRepository.cost_breakdown` (GROUP BY role+model, cost-desc); `GET /tasks/{id}/spans` (duration_ms computed) + `GET /tasks/{id}/costs` (total + rows). 2 integration tests (seeded tree linkage + attrs through API; empty-task shape). Backend 168 tests green.
- Frontend: `TracePanel` in TaskDetail (lazy fetch on expand) — cost table (role/model/calls/tokens/cost, total headline) + span tree (parent_id → indented tree, duration bars scaled to max, kind-toned names, llm spans show model+token flow, failed tools flagged). Build green (72KB gzip).
- NOT committed.

### 2026-09-24 — Step 35: Approvals queue UI ✅
- `ApprovalQueue`: pending list (5s poll, renders nothing when empty), amber needs-human section w/ count badge; per-card: level+reason chips, task/subtask ids, proposed action, expandable context (the policy-written summary), note input, approve/reject → decide endpoint → resume enqueued. 409 (already decided elsewhere) silently drops the card — the CLI/UI double-decide race handled as UX, optimistic removal on success, `onDecided` refreshes the task list.
- Full HITL loop now closable in the browser: submit w/ plan approval → banner + queue card → approve → watch resume via SSE.
- Build green (71.5KB gzip). NOT committed.

### 2026-09-24 — Step 34: Task detail + live SSE ✅
- `useTaskStream` hook: the fetch-then-stream contract client-side — REST snapshot, EventSource for named deltas (all 8 event types), snapshot refetch on terminal + escalation/resume flips, cleanup closes the source, `live` indicator state.
- `TaskDetail`: event log folded into render state (plan tree from plan_created incl. "informed by N memories", per-subtask outcome chips status·score·confidence·revisions, queued placeholders), amber escalation banner (role=alert) pointing to approvals, final output pane. `key={taskId}` remount for clean per-task state.
- Build green (tsc strict + vite, 71KB gzip). NOT committed.

### 2026-09-22 — Step 33: Frontend scaffold ✅ (Phase 8 begins; step 32 replay deferred)
- Vite react-ts scaffold; `src/api/` typed client (Task/Approval/TaskEvent mirror backend response models; ApiError w/ status; streamUrl for EventSource) — fetch-based, no client lib needed at this scale.
- Ops-console design direction (per design rules, not template defaults): dark phosphor-on-slate tokens (oklch), JetBrains Mono accents, status badges color-coded by tone (awaiting_approval renders as "needs human" in amber).
- Features: NewTaskForm (require_review checkbox → plan-approval flow), TaskList w/ selection, App with 4s list polling (per-task SSE arrives step 34). a11y: labeled form/list, focus-visible states.
- Build gate: `npm run build` = tsc strict + vite. Hit two live snags: TS `erasableSyntaxOnly` (no ctor param properties), npm optional-deps bug → rolldown darwin-arm64 binding installed explicitly. Bundle 70KB gzip. `make frontend`/`frontend-build` targets.
- NOT committed (per standing instruction).

### 2026-09-22 — Step 31: OpenTelemetry tracing ✅
- `observability/otel.py`: `PostgresSpanExporter` (sync psycopg on BatchSpanProcessor's thread — the normal exporter shape) → spans table rows: deterministic UUIDs from OTel trace/span ids (tree via parent_id), task_id/kind lifted from marker attrs into columns, rest into JSONB, ON CONFLICT DO NOTHING. Fail-open. `setup_tracing()` once per process (worker entrypoints); `force_flush()` before task outcome reported.
- Instrumented via the global no-op-until-configured OTel API (zero config cost elsewhere): graph nodes (`graph.*`, interrupts NOT marked as errors — a pause isn't a failure), LLM turns in AgentRuntime (`llm.complete` w/ model/provider/tokens/stop_reason/turn/specialist), tool executions in registry (`tool.<name>` w/ success/latency, wrapping the whole middleware pipeline).
- 3 tests via InMemorySpanExporter (node-span tree + task_id attrs incl. Send-payload work nodes; row-mapping pure function: parent linkage, kind/task extraction, attrs JSON). `make check` green (166 tests).

### 2026-09-21 — Step 30.5: MCP adapter ✅ (restored CUT item delivered)
- `tools/mcp_adapter.py`: `MCPTool` wraps a remote MCP tool as a registry entry — `to_spec()` overridden to hand the LLM the SERVER's JSON schema verbatim; args pass through permissively (extra=allow) and the MCP server enforces its own schema, errors flowing back via the normal error-result channel. `discover_mcp_tools(session)` lists+wraps with `mcp_` prefix. External tools get the full middleware pipeline (logging/limits/sensitive gate) for free.
- mcp SDK 2.2.0 (hit the 2.x renames live: FastMCP→MCPServer, inputSchema→input_schema).
- 4 tests against a real in-process MCP server over memory streams (discovery+schema export, registry execution, server-side validation error LLM-readable, spec export). `make check` green (163 tests).

### 2026-09-21 — Step 30: SSE streaming ✅
- `api/routes/stream.py`: `GET /tasks/{id}/stream` — StreamingResponse over `subscribe_events`, SSE wire format (named events + JSON data), 15s ping comments (proxy keep-alive), `X-Accel-Buffering: no`, closes itself on terminal events, 404 on unknown task. Fetch-then-stream client contract + SSE-over-WebSocket rationale documented in module.
- Internally: pump-task + queue so pings interleave with events; pump cancelled on disconnect (generator finally).
- 2 tests: full pipeline (POST task → open stream in-process → publish via real Redis → named events parsed → stream self-closes on task_finished), 404. `make check` green (159 tests).

### 2026-09-21 — Step 29: FastAPI ✅ (Phase 7 begins)
- `api/deps.py`: lifespan owns process clients (engine/sessions/redis in AppState); `get_session` = one session per request, commit-on-success/rollback-on-error; `Annotated[..., Depends]` aliases (SessionDep etc.).
- Routes: `POST /tasks` 202 create+enqueue (commit BEFORE .delay — row must exist when worker grabs it), get/list; `GET /approvals` pending, `POST /approvals/{id}/decision` → 409 on double-decide, commit-then-enqueue-resume; `DELETE /memory/task/{id}` (503 when memory unconfigured/down); /health; CORS for Vite dev origin. `make api`.
- API response models separate from domain (TaskResponse etc. — the DTO boundary).
- 4 API tests via httpx ASGITransport + LifespanManager (in-process, real Postgres, celery .delay stubbed to a recorder): create-persists-enqueues, 422 validation, 404, approval flow incl. resume enqueue + 409 idempotency. Live uvicorn smoke: health + create OK. `make check` green (157 tests).

### 2026-09-20 — Step 28: Granular approval levels ✅ (Phase 6 complete; take-over stays CUT)
- NOTIFY level live: `EscalationPolicy.notify_reasons` (default: {low_confidence}) maps reasons to non-blocking notices via `level_for()`; escalation_gate resolves NOTIFY escalations without interrupting — subtask proceeds as accepted_with_issues + escalation_raised event (level=notify). Weak signal now informs instead of blocking; empty set restores blocking (pinned by test).
- `hitl/middleware.py`: `SensitiveToolGate` — the codebase's one FAIL-CLOSED middleware: sensitive tools denied unless (task, tool) allowance granted; denial message instructs the LLM to proceed without and report. Deny-over-interrupt rationale documented (interrupt mid-work re-runs LLM calls → approved-vs-executed drift).
- 6 new tests (notify no-pause + events, configurability, gate deny/allow/per-task/non-sensitive). Step-27 low-confidence test updated to explicit blocking policy. `make check` green (153 tests).

### 2026-09-20 — Step 27: Durable pause/resume ✅ (the marquee)
- Graph: two interrupt-isolated gate nodes — `plan_gate` (after supervise; evaluate_plan → interrupt; reject = all subtasks failed, nothing runs) and `escalation_gate` (work-join; parked ESCALATED subtasks resolved one interrupt per human decision; approve → accepted_with_issues, reject → failed+cascade). Work node now parks via `evaluate_result` instead of failing. Footgun handled by construction: interrupts live in pure gate nodes, so resume re-execution re-runs no LLM/tool work — pinned by `test_work_not_reexecuted_on_resume`.
- Worker: `_run_segment` shared by fresh + resume paths; `__interrupt__` in final state → ApprovalRow + AWAITING_APPROVAL + escalation_raised event + Celery task ENDS (durable pause, no slot burned). `resume_graph_task(task_id, approval_id)` → `Command(resume=decision)` on same thread_id.
- `ApprovalRepository` (create/get/decide-with-idempotency-guard/list_pending); `ApprovalDecisionInput` resume payload schema; EventTypes escalation_raised/task_resumed; `hitl/cli.py` list|approve|reject → enqueues resume.
- 6 interrupt tests (pause payload, approve-continues, reject-cascades, no-reexecution, plan gate both ways, low-confidence trigger); old escalate-test updated to new contract. `make check` green (120 unit; integration pending docker).

### 2026-09-07 — Step 26: Escalation policy engine ✅ (Phase 6 begins)
- `hitl/policy.py`: pure decision functions, zero I/O — `evaluate_plan` (user require_review flag / policy-wide sign-off → APPROVE_PLAN), `evaluate_result` (reviewer-escalate > repeated-failure > low-score > low-confidence, objective-first priority → APPROVE_ACTION), `evaluate_tool` (sensitive flag → APPROVE_ACTION w/ args preview). `EscalationPolicy` thresholds frozen+validated; floors exclusive ("below", not "at").
- `Task.require_review` added end-to-end: schema, TaskRow (+server_default), repo mapping, migration 41de93b48419 applied.
- Settings: escalation_confidence_floor / escalation_review_floor / require_plan_approval (ops-tunable).
- 12 pure tests (every trigger, priority, boundary, plan-in-context). `make check` green (141 tests).

### 2026-09-07 — Step 25: Event bus ✅ (Phase 5 complete)
- `observability/events.py`: TaskEvent (typed, frozen), EventType enum, `EventPublisher` (fire-and-forget, fail-open), `subscribe_events()` async iterator ending on terminal events. Channel `task:{id}:events`; documented that pub/sub channels are server-global (DBs don't namespace) and replay-free (durable history = Postgres; bus = liveness).
- Graph nodes emit via optional `GraphDeps.emit`: plan_created (subtasks + memories_used count), dispatching (ready batch), subtask_completed (status/score/confidence/revisions), task_finished. Worker adds task_started/task_failed boundaries; publisher wired in bundle.
- `observability/tail.py`: live CLI tail (subscribe before enqueue — no replay).
- 2 tests: pub/sub roundtrip vs live Redis (terminal-event shutdown), full emission sequence via collector (no Redis). `make check` green (129 tests).

### 2026-09-06 — Step 24: Celery worker ✅
- `worker/celery_app.py`: Redis broker /0, results /1 (documented map); json-only, acks_late + reject_on_worker_lost (lost worker requeues, doesn't eat tasks), prefetch=1 (graph runs are minutes). `--pool=solo` with the asyncio rationale in the docstring; `make worker` target.
- `worker/deps.py`: `build_runtime_bundle()` — ALL async clients built inside the running loop (loop-binding rule): providers, router, default registry (factory extracted, CLI reuses), engine/sessions, optional Chroma memory + consolidator (graceful degrade), specialist cost-recording callback → llm_calls.
- `worker/tasks.py`: `execute_task()` — PENDING→RUNNING→COMPLETED/FAILED in Postgres, graph under Postgres checkpointer (thread_id = task_id), results→working memory→consolidation→WM clear. Sync celery shim `run_graph_task` = `asyncio.run(...)`. `worker/enqueue.py` helper CLI.
- `redis_url_with_db()` extracted to config. Worker *container* deferred to step 37 (needs backend image).
- 3 integration tests (lifecycle vs live pg+redis, missing row, failure marks FAILED). mypy override for untyped celery. `make check` green (127 tests).

### 2026-09-06 — Step 23: Postgres checkpoints + parallel fan-out ✅
- Graph reshaped: specialist+review+revise merged into atomic `work` node; dispatch computes the whole ready batch and fans out via `Send` (one work node per subtask, same superstep = true parallelism; work→dispatch edge is the join barrier). State dicts now carry `merge_dicts` reducers — parallel siblings can't clobber each other; nodes return only changed entries.
- `graph/checkpointing.py`: `postgres_checkpointer()` ctx manager — AsyncPostgresSaver over psycopg DSN (asyncpg dialect stripped), idempotent `setup()`. Checkpointer tables excluded from Alembic autogen (env.py include_object); `alembic check` confirms no drift.
- Tests: concurrency proven by interval overlap (s1/s2 parallel, s3 after both, both results survive merge); checkpoint durability: new graph instance over same saver recovers final state + ≥4-checkpoint history. `make check` green (124 tests).

### 2026-09-03 — Step 22: LangGraph skeleton ✅
- `graph/state.py`: OrchestratorState TypedDict — plan, results/reviews/revision_counts/subtask_status dicts, `errors` with `operator.add` reducer, explicit `next_action` routing field.
- `graph/nodes.py`: supervise → dispatch (picks next runnable, settles failure cascades) → specialist (context = dependency outputs + revision feedback) → review (verdict → status transitions: accepted / accepted_with_issues after MAX_REVISIONS=1 / failed on escalate) → synthesize (mechanical assembly, dependency-ordered). Deps injected via `GraphDeps` Protocols.
- `graph/build.py`: StateGraph wiring, conditional edges on dispatch (specialist|synthesize) + review (revise→specialist | continue→dispatch), InMemorySaver default.
- `graph/cli.py`: live end-to-end run streaming node updates (needs API keys — not yet run live).
- 4 graph tests: dependency order + context flow, revise-loop-once w/ feedback, revision-budget→accepted_with_issues (pipeline continues), escalate→cascade fail. `make check` green (121 tests).

### 2026-08-30 — Step 21: Reviewer ✅
- `agents/reviewer.py`: judges AgentResult against its SubTask contract via `complete_structured`. Internal `_Judgment` model (score/verdict/issues/feedback) — LLM never echoes ids, we attach subtask_id ourselves. Rubric: contract fit, completeness, groundedness, usability; verdict rules force actionable revise-feedback; anti-0.7-clustering instruction. Runtime error flags (turn budget, refusal) surfaced to the reviewer as "Execution flag". Output excerpt capped 8K.
- 4 tests (verdict mapping, actionable feedback, prompt contents incl. error flag, truncation). `make check` green (117 tests).

### 2026-08-30 — Step 20: Supervisor ✅
- `agents/supervisor.py`: task → memory retrieval (narrow `MemorySearch` Protocol; LongTermMemory satisfies structurally) → memories injected into planning prompt → `complete_structured(response_model=Plan)`. Plan's own validators (cycles/dangling deps/dupes) are the retry acceptance criteria: unexecutable plans bounce back to the model with the error. Returns `PlanningOutcome(plan, memories_used)` for the "memory informed this decision" trace.
- System prompt: specialist catalog, 2-6 subtasks, parallel-unless-dependent, workspace file-handoff instructions.
- 4 tests incl. cycle-rejected-then-retried and memory injection. `make check` green (113 tests).

### 2026-08-30 — Step 18 (+19): Specialists ✅
- `agents/specialists.py`: 4 configs — research (web_search+files, cites+saves findings), writer (files only, reads teammates' artifacts), data_analysis (run_python, "compute don't guess"), code_execution (run_python, error-fix-rerun loop, max_turns=10). `build_specialist()` resolves the specialist route from the router and assembles the runtime.
- Least-privilege tools per specialist (analysis/code get no web_search). Workspace = artifact hand-off convention baked into prompts.
- 4 tests incl. prompts-mention-their-tools guard + end-to-end build smoke. `make check` green (109 tests).

### 2026-08-26 — Step 17b: Generic agent runtime ✅
- `agents/runtime.py`: `AgentRuntime` — the loop: complete() → tool_calls? → registry.execute (parallel, asyncio.gather) → echo AssistantToolCallMessage + ToolResultMessage → repeat. Ends on: final text (confidence marker parsed+stripped, default 0.5), refusal (conf 0), or turn budget (conf 0.2, error flagged). Tool failures are conversation content; `on_llm_response` callback = cost-recording seam.
- Confidence: `CONFIDENCE: <0-1>` line in final answer (single-pass, cheap) vs extra self-assessment call — chose marker.
- fakes upgraded (tool_call_response). 8 runtime tests. `make check` green (105 tests).

### 2026-08-26 — Step 17a: Tool-calling conversations ✅ (17 split into a/b)
- `llm/types.py`: `AssistantToolCallMessage` (echoed assistant turn w/ tool calls), `ToolResult` + `ToolResultMessage`, `ConversationMessage` union. Protocol + both adapters now accept full tool conversations.
- Anthropic mapping: assistant → content blocks (text + tool_use); results → USER-role tool_result blocks. OpenAI mapping: assistant → `tool_calls` field w/ JSON-string args; results → one `tool`-role message each.
- 5 wire-format mapping tests (the provider-contrast documentation). `make check` green (97 tests).

### 2026-08-25 — Step 16: Memory consolidation + structured output ✅ (Phase 3 complete)
- `llm/structured.py`: `complete_structured[T]` — provider-agnostic JSON-mode: schema injected into system prompt, fence-tolerant parse, retry loop feeds validation errors back to the model, `StructuredOutputError` after max attempts. Reused by supervisor (step 20) + reviewer (step 21).
- `memory/lifecycle.py`: `MemoryConsolidator` — at task completion, LLM distills working memory (plan+results+errors) into ≤5 typed memories with importance per the rubric-in-schema; writes to Chroma tagged with source task id.
- `tests/fakes.py`: shared `ScriptedProvider`. tests/ became a package (mypy dual-module fix).
- 8 new tests (structured: parse/fences/retry-feedback/exhaustion; consolidation vs live Redis+Chroma with scripted LLM). `make check` green (92 tests).

### 2026-08-25 — Step 15: Long-term memory (Chroma) ✅
- `memory/embedder.py`: `Embedder` Protocol + `OpenAIEmbedder` (text-embedding-3-small). Embeddings are ours, not Chroma's — server stays a pure vector store, tests inject a deterministic fake.
- `memory/longterm.py`: `LongTermMemory` — MemoryEntry {kind: task_summary|fact|preference, importance, task_id, created_at}, cosine collection, importance-weighted retrieval (overfetch 4x by similarity → rerank 0.7·sim + 0.3·importance), `delete_for_task()` user-data wipe, count.
- Fixed real version skew: pip client 1.5.9 vs server image 0.6.3 → API 500s (`KeyError '_type'`). Compose image bumped to 1.5.9 with a keep-aligned comment.
- 4 integration tests (nearest, importance tie-break, kind filter, task wipe) via FakeEmbedder — no embedding API calls. `make check` green (84 tests).

### 2026-08-25 — Step 14: Working memory (Redis) ✅ (step 13 skipped per plan TRIM/CUT)
- `memory/working.py`: `WorkingMemory` — task-scoped scratchpad, 3 fixed keys per task (plan STRING / results HASH by subtask / errors LIST), typed Pydantic in/out, 24h TTL leak guard, O(1) `clear()`. `create_working_memory_client()` pins Redis logical DB /2 per the documented DB map.
- Why not LangGraph state: must survive across Celery invocations (pause/resume) and be readable by consolidation after the run.
- 6 integration tests (roundtrip typed, accumulation, order, clear, task isolation). `make check` green (80 tests).

### 2026-08-12 — Step 12: Sandboxed code execution ✅
- `tools/builtin/code_exec.py`: `run_python` — ephemeral Docker container per call: `--network=none`, 256M/1cpu/128pids, `--read-only` + noexec tmpfs, user nobody, code via stdin (not argv — visible in `docker inspect`), hard asyncio timeout → `docker kill`, output capped 20K. Tracebacks raised back to LLM for self-repair. Docker driven via async subprocess CLI (docker-py SDK is sync — would block the loop).
- Threat-model honesty in the docstring: container escape out of scope; prod = gVisor/Firecracker/E2B.
- 2 unit tests (policy flags) + 5 integration tests (stdout, traceback, network blocked, infinite-loop killed + no orphan container, rootfs read-only). `make check` green (74 tests).

### 2026-08-12 — Step 11: Tool middleware ✅
- `tools/middleware.py`: `ToolInvocationLogger` — every invocation → tool_invocations row (own short-lived session, output capped at 10K, fail-open with warning). `ToolRateLimiter` — Redis token bucket via atomic Lua script (capacity=burst, refill/s=sustained rate), per-tool limits, unlimited tools pass through, fails open if Redis down, denial returns LLM-readable retry message.
- Redis chosen over in-process counters: API + N Celery workers share one budget.
- 3 integration tests (burst-then-deny, passthrough, persisted row w/ JSONB query + cleanup). `make check` green (67 tests).

### 2026-08-12 — Step 10: File tools + web search ✅
- `tools/builtin/files.py`: `read_file` / `write_file`, workspace-confined — resolve() + is_relative_to guard rejects `..`, absolute paths, AND symlink escapes (tested). Read truncation at 100K chars, write cap via schema max_length, parents auto-created.
- `tools/builtin/web_search.py`: Tavily via httpx, injectable transport for tests, LLM-friendly numbered output, HTTP errors propagate (registry converts to error results).
- `Settings.workspace_dir` (default `sandbox-workspace/`, gitignored since step 1).
- 11 new tests (traversal suite, MockTransport request/response mapping). `make check` green (64 tests).

### 2026-08-06 — Step 9: Tool registry ✅
- `tools/base.py`: `Tool[ArgsT]` generic ABC (PEP 695 syntax) — name/description/`sensitive` flag (HITL hook), Pydantic args model doubling as validator + LLM-facing JSON schema, `to_spec()` exports the neutral ToolSpec (adapters already handle provider dialects). `InvocationContext`, `ToolExecutionResult` (errors are data, not exceptions).
- `tools/registry.py`: register/get/specs + `execute()` pipeline: validate args → middleware onion (Express-style, step 11 plugs in logging/rate limits) → run. Unknown tool, invalid args, and tool exceptions all become error results the LLM can react to.
- 9 new tests incl. middleware ordering + short-circuit. `make check` green (53 tests).

### 2026-08-06 — Step 8: Cost tracking ✅
- `llm/pricing.py`: static USD-per-MTok table (sonnet-5, haiku-4-5, opus-5, gpt-5, gpt-5-mini) + `compute_cost_usd()` — unknown model logs warning and costs 0 (observability never breaks execution).
- `db/repositories.py`: `LLMCallRepository.record()` (response + role + task_id → llm_calls row with computed cost) and `total_cost_for_task()` (SQL SUM).
- Fixed test-isolation bug found by the suite: structlog `PrintLoggerFactory(sys.stdout)` captured pytest's per-test stdout → writes to closed file. Now resolves stdout at call time + autouse reset fixture in logging tests.
- 5 new tests. `make check` green (44 tests).

### 2026-08-06 — Step 7: OpenAI adapter + model router ✅
- `llm/openai_provider.py`: second adapter proving the Protocol — system as message role (vs Anthropic top-level param), tool args JSON-string parsing with error context (vs dict), prompt/completion_tokens normalization, finish_reason mapping (content_filter→refusal).
- `llm/router.py`: `ModelRouter` — role→"provider:model" routes from Settings (supervisor sonnet-5, specialist haiku-4-5, reviewer gpt-5), `LLM_CHEAP_MODE` dev flag downgrades every route, actionable errors for missing keys/bad specs. `build_providers()` constructs adapters only for configured keys.
- 11 new tests (OpenAI mapping fixtures, router resolution/cheap-mode/errors). `make check` green (39 tests).

### 2026-08-05 — Step 6: LLM protocol + Anthropic adapter ✅
- `llm/types.py`: provider-neutral frozen models — ChatMessage, ToolSpec, ToolCall, Usage, StopReason, LLMResponse.
- `llm/provider.py`: `LLMProvider` Protocol (structural typing — adapters satisfy by shape).
- `llm/anthropic_provider.py`: AsyncAnthropic adapter — system as top-level param, tools dialect conversion, content-block parsing (text + tool_use), stop-reason mapping, latency capture. Retries via SDK `max_retries` (dropped planned tenacity — SDK already does exponential backoff on 429/5xx/connection errors).
- `llm/smoke.py`: CLI one-shot (`uv run python -m orchestrator.llm.smoke "..."`), uses claude-haiku-4-5. **Not yet run live — no .env/API key present.**
- 5 fixture-based mapping tests (real SDK objects, no network). `make check` green (28 tests).

### 2026-08-03 — Step 5: Postgres layer ✅
- `db/base.py`: DeclarativeBase + constraint naming convention (reproducible migrations).
- `db/models.py`: 6 tables — tasks, plan_steps, approvals, tool_invocations, llm_calls, spans. Enums stored as strings; tz-aware timestamps; JSONB for depends_on/arguments/attributes.
- `db/session.py`: engine/session **factories** (no module-level singletons — event-loop binding).
- `db/repositories.py`: TaskRepository mapping ORM rows ↔ domain models at the boundary.
- Alembic (async template) wired to Settings + Base.metadata; initial migration applied.
- Integration tests (transaction-rollback fixture, auto-skip if pg down). `make check` green (23 tests).
- Gotcha caught by test: Postgres `now()` = transaction start time — same-transaction rows share created_at.

### 2026-08-03 — Step 4: Domain schemas ✅
- `schemas/enums.py`: StrEnums — TaskStatus, SubTaskStatus, SpecialistType, AgentRole, ReviewVerdict, EscalationReason, ApprovalLevel, ApprovalDecision.
- `schemas/domain.py`: frozen Pydantic models — Task, SubTask, Plan (dependency validation: unknown refs, dupes, self-deps, cycles via Kahn; `execution_batches()` for parallel scheduling), AgentResult (confidence 0–1), ReviewResult (rubric score + verdict), Escalation.
- Field descriptions written as LLM prompt material (structured-output targets).
- 12 new tests. `make check` green (19 tests).

### 2026-08-03 — Step 3: Config + logging ✅
- `config.py`: `Settings` (pydantic-settings) — env/.env-loaded, typed, `SecretStr` for API keys, `lru_cache`d `get_settings()`. `.env.example` at root documents all vars + Redis logical DB map.
- `logging.py`: structlog — pretty console in dev, JSON lines in prod, level filtering, contextvars merging.
- 6 new tests (defaults, env override, invalid env rejected, secret masking, JSON shape, level filter). `make check` green (7 tests).

### 2026-07-28 — Step 2: Infra docker-compose ✅
- `docker-compose.yml`: postgres:16-alpine (port 5432), redis:7-alpine with AOF (6379), chromadb 0.6.3 server mode (host 8001 → container 8000). Named volumes + healthchecks on all three.
- Makefile: `infra-up / infra-down / infra-ps`.
- Verified: pg_isready, redis PONG, chroma v2 heartbeat.

### 2026-07-27 — Step 1: Scaffold + tooling ✅
- `uv init --lib` backend with src layout (`backend/src/orchestrator/`), Python 3.12 pinned via uv.
- Dev tooling: ruff (lint+format), mypy strict, pytest + pytest-asyncio (auto mode). All config in `pyproject.toml`.
- Root `Makefile`: `install / test / lint / format / typecheck / check`.
- Smoke module `orchestrator.health()` + first test. `make check` green.
- Wrote `docs/PLAN.md` (static build plan) and this file.
- Repo git-initialized (no commits yet).

## Decisions Made So Far

- uv over poetry/pip; ruff over black+flake8+isort; mypy strict; src layout over flat. (Details → docs/DECISIONS.md, started next steps.)

## How to Run

```bash
make install   # sync backend deps (uv)
make check     # lint + typecheck + tests
```
