"""Structured logging + OpenTelemetry tracing for the agent system."""
from __future__ import annotations

import json
import logging
import os
import sys
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

from opentelemetry import trace
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor, SpanExporter, SpanExportResult

from .config import get_settings

STATE_DIR = Path("state")
STATE_DIR.mkdir(exist_ok=True)
TRACE_FILE = STATE_DIR / "traces.jsonl"


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(record.created)),
            "lvl": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        for key in ("agent", "run_id", "tool", "signal_id"):
            value = getattr(record, key, None)
            if value is not None:
                payload[key] = value
        return json.dumps(payload, ensure_ascii=False)


def _configure_logging() -> None:
    root = logging.getLogger()
    if getattr(root, "_fibreops_configured", False):
        return
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(os.getenv("FIBREOPS_LOG_LEVEL", "INFO"))
    root._fibreops_configured = True  # type: ignore[attr-defined]


class JsonlSpanExporter(SpanExporter):
    """Persist spans to JSONL so the optimiser can replay agent traces."""

    def export(self, spans) -> SpanExportResult:  # type: ignore[override]
        with TRACE_FILE.open("a", encoding="utf-8") as f:
            for span in spans:
                f.write(
                    json.dumps(
                        {
                            "name": span.name,
                            "trace_id": format(span.context.trace_id, "032x"),
                            "span_id": format(span.context.span_id, "016x"),
                            "parent_id": format(span.parent.span_id, "016x") if span.parent else None,
                            "start": span.start_time,
                            "end": span.end_time,
                            "duration_ms": (span.end_time - span.start_time) / 1_000_000,
                            "attributes": dict(span.attributes or {}),
                            "status": span.status.status_code.name,
                        },
                        default=str,
                    )
                    + "\n"
                )
        return SpanExportResult.SUCCESS

    def shutdown(self) -> None:  # pragma: no cover
        return None


_tracing_configured = False


def _configure_tracing() -> None:
    global _tracing_configured
    if _tracing_configured:
        return
    resource = Resource.create({"service.name": "fibreops"})
    provider = TracerProvider(resource=resource)
    provider.add_span_processor(BatchSpanProcessor(JsonlSpanExporter()))
    settings = get_settings()
    if settings.applicationinsights_connection_string:
        try:
            from azure.monitor.opentelemetry import configure_azure_monitor

            configure_azure_monitor(connection_string=settings.applicationinsights_connection_string)
        except Exception as exc:  # pragma: no cover
            logging.getLogger(__name__).warning("App Insights init failed: %s", exc)
    trace.set_tracer_provider(provider)
    _tracing_configured = True


def init_observability() -> None:
    _configure_logging()
    _configure_tracing()


def get_logger(name: str) -> logging.Logger:
    _configure_logging()
    return logging.getLogger(name)


def get_tracer(name: str = "fibreops"):
    _configure_tracing()
    return trace.get_tracer(name)


@contextmanager
def agent_span(agent_name: str, run_id: str, **attrs: Any) -> Iterator[Any]:
    tracer = get_tracer()
    with tracer.start_as_current_span(f"agent.{agent_name}") as span:
        span.set_attribute("agent.name", agent_name)
        span.set_attribute("run.id", run_id)
        for k, v in attrs.items():
            if v is not None:
                span.set_attribute(k, v)
        yield span


@contextmanager
def tool_span(tool_name: str, **attrs: Any) -> Iterator[Any]:
    tracer = get_tracer()
    with tracer.start_as_current_span(f"tool.{tool_name}") as span:
        span.set_attribute("tool.name", tool_name)
        for k, v in attrs.items():
            if v is not None:
                span.set_attribute(k, v)
        yield span


@contextmanager
def orchestrator_span(run_id: str, **attrs: Any) -> Iterator[Any]:
    """Top-level span wrapping a single signal-to-dispatch run.

    Lands in App Insights ``dependencies`` table with name
    ``orchestrator.handle_signal``. All child agent/tool spans inherit
    ``operation_Id`` so the KQL trace-replay query can stitch them back.
    """
    tracer = get_tracer()
    with tracer.start_as_current_span("orchestrator.handle_signal") as span:
        span.set_attribute("run.id", run_id)
        for k, v in attrs.items():
            if v is not None:
                span.set_attribute(k, v)
        yield span


def record_event(name: str, **attrs: Any) -> None:
    """Emit an OpenTelemetry span event on the current span.

    Span events surface in the App Insights ``traces`` table with ``message``
    set to ``name`` and the attributes flattened into ``customDimensions``.
    """
    current = trace.get_current_span()
    if current is None:
        return
    clean = {k: v for k, v in attrs.items() if v is not None}
    try:
        current.add_event(name, attributes=clean)
    except Exception:  # pragma: no cover
        pass
