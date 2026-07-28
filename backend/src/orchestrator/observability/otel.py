"""OpenTelemetry setup: spans persisted to our Postgres spans table.

Instrumentation pattern: code everywhere calls the global OTel API
(trace.get_tracer(...)), which is a NO-OP until a provider is configured —
so graph nodes, the agent runtime, and the tool registry are instrumented
unconditionally at zero cost, and only processes that call setup_tracing()
(the worker) actually record and export.

Export path: BatchSpanProcessor hands finished spans to PostgresSpanExporter
on a background thread; the exporter is sync by contract, so it uses a sync
psycopg connection — sync-in-a-dedicated-thread is the normal shape for OTel
exporters, not a violation of our async rules.
"""

import json
from typing import Any
from uuid import UUID

import psycopg
from opentelemetry import trace
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import ReadableSpan, TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor, SpanExporter, SpanExportResult

from orchestrator.config import Settings
from orchestrator.graph.checkpointing import psycopg_dsn
from orchestrator.logging import get_logger

logger = get_logger(__name__)

TASK_ID_ATTR = "orchestrator.task_id"
KIND_ATTR = "orchestrator.kind"  # graph_node | llm_call | tool_call | task

_INSERT = """
INSERT INTO spans (id, task_id, parent_id, name, kind, status, attributes, started_at, ended_at)
VALUES (%s, %s, %s, %s, %s, %s, %s, to_timestamp(%s), to_timestamp(%s))
ON CONFLICT (id) DO NOTHING
"""


def _span_uuid(trace_id: int, span_id: int) -> UUID:
    """Deterministic UUID from OTel ids (span ids are 64-bit, our PK is UUID)."""
    return UUID(int=((trace_id & ((1 << 64) - 1)) << 64) | span_id)


def _row(span: ReadableSpan) -> tuple[Any, ...] | None:
    context = span.get_span_context()
    if context is None or span.start_time is None:
        return None
    attributes = dict(span.attributes or {})
    task_id = attributes.pop(TASK_ID_ATTR, None)
    kind = attributes.pop(KIND_ATTR, "internal")
    parent = (
        _span_uuid(context.trace_id, span.parent.span_id) if span.parent is not None else None
    )
    return (
        _span_uuid(context.trace_id, context.span_id),
        str(task_id) if task_id else None,
        parent,
        span.name,
        str(kind),
        span.status.status_code.name.lower(),
        json.dumps(attributes, default=str),
        span.start_time / 1e9,  # OTel times are epoch nanoseconds
        (span.end_time or span.start_time) / 1e9,
    )


class PostgresSpanExporter(SpanExporter):
    """Writes finished spans as rows the trace-explorer UI can query."""

    def __init__(self, dsn: str) -> None:
        self._dsn = dsn

    def export(self, spans: Any) -> SpanExportResult:
        rows = [r for s in spans if (r := _row(s)) is not None]
        if not rows:
            return SpanExportResult.SUCCESS
        try:
            with psycopg.connect(self._dsn) as conn, conn.cursor() as cur:
                cur.executemany(_INSERT, rows)
            return SpanExportResult.SUCCESS
        except Exception:  # noqa: BLE001 — observability must not break execution
            logger.warning("span_export_failed", count=len(rows), exc_info=True)
            return SpanExportResult.FAILURE

    def shutdown(self) -> None:  # interface requirement
        return None


_configured = False


def setup_tracing(settings: Settings) -> None:
    """Install the provider once per process (worker startup)."""
    global _configured
    if _configured:
        return
    provider = TracerProvider(
        resource=Resource.create({"service.name": "agent-orchestrator"})
    )
    provider.add_span_processor(
        BatchSpanProcessor(PostgresSpanExporter(psycopg_dsn(settings)))
    )
    trace.set_tracer_provider(provider)
    _configured = True


def force_flush() -> None:
    """Drain pending spans — call at the end of a task segment so rows land
    before the task reports finished."""
    provider = trace.get_tracer_provider()
    flush = getattr(provider, "force_flush", None)
    if callable(flush):
        flush(timeout_millis=5_000)
