"""Trace context helpers."""

from __future__ import annotations

import contextvars
import secrets

_trace_id: contextvars.ContextVar[str | None] = contextvars.ContextVar("pynetscope_trace_id", default=None)


def current_trace_id() -> str | None:
    return _trace_id.get()


def set_trace_id(trace_id: str | None) -> None:
    _trace_id.set(trace_id)


def ensure_trace_id() -> str:
    existing = current_trace_id()
    if existing:
        return existing
    trace_id = secrets.token_hex(16)
    set_trace_id(trace_id)
    return trace_id


def traceparent(trace_id: str | None = None, span_id: str | None = None) -> str:
    selected_trace_id = trace_id or ensure_trace_id()
    selected_span_id = span_id or secrets.token_hex(8)
    return f"00-{selected_trace_id}-{selected_span_id}-01"


def inject_headers(headers: dict[str, str] | None = None) -> dict[str, str]:
    next_headers = dict(headers or {})
    next_headers.setdefault("traceparent", traceparent())
    next_headers.setdefault("x-request-id", ensure_trace_id())
    return next_headers
