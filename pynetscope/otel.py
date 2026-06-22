"""Optional OpenTelemetry bridge helpers."""

from __future__ import annotations

from typing import Any

from .models import RequestRecord


def span_attributes(record: RequestRecord) -> dict[str, Any]:
    """Return semantic-ish span attributes for a recorded request."""

    return {
        "http.request.method": record.method,
        "url.full": record.url,
        "http.response.status_code": record.status_code,
        "pynetscope.endpoint": record.endpoint,
        "pynetscope.latency_ms": record.latency_ms,
        "pynetscope.ok": record.ok,
        "pynetscope.error": record.error,
        "trace.id": record.trace_id,
    }


def annotate_current_span(record: RequestRecord) -> bool:
    """Annotate the current OpenTelemetry span when opentelemetry-api exists.

    Returns True when a recording span was found, otherwise False.
    """

    try:
        from opentelemetry import trace
    except ImportError:
        return False

    span = trace.get_current_span()
    if not span or not getattr(span, "is_recording", lambda: False)():
        return False
    for key, value in span_attributes(record).items():
        if value is not None:
            span.set_attribute(key, value)
    if record.error:
        span.record_exception(Exception(record.error))
    return True


def create_span(record: RequestRecord) -> bool:
    """Create a new OpenTelemetry span representing the recorded request in the past."""
    try:
        from opentelemetry import trace
    except ImportError:
        return False

    tracer = trace.get_tracer("pynetscope")
    start_time_ns = int((record.timestamp_ms - record.latency_ms) * 1_000_000)
    end_time_ns = int(record.timestamp_ms * 1_000_000)

    span = tracer.start_span(
        name=f"{record.method} {record.endpoint}",
        start_time=start_time_ns,
        attributes=span_attributes(record),
    )
    if record.error:
        span.record_exception(Exception(record.error))
        span.set_status(trace.status.Status(trace.status.StatusCode.ERROR, record.error))
    else:
        status_code = trace.status.StatusCode.OK if record.ok else trace.status.StatusCode.ERROR
        span.set_status(trace.status.Status(status_code))
    span.end(end_time=end_time_ns)
    return True


def setup_otlp_exporter(endpoint: str = "http://localhost:4317") -> bool:
    """Configure OTLP traces exporter if opentelemetry-sdk is installed."""
    try:
        from opentelemetry import trace
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor
    except ImportError:
        return False

    try:
        from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
    except ImportError:
        try:
            from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
        except ImportError:
            return False

    try:
        provider = trace.get_tracer_provider()
    except Exception:
        provider = None

    if provider is None or not hasattr(provider, "add_span_processor"):
        provider = TracerProvider()
        trace.set_tracer_provider(provider)

    exporter = OTLPSpanExporter(endpoint=endpoint)
    if hasattr(provider, "add_span_processor"):
        provider.add_span_processor(BatchSpanProcessor(exporter))  # type: ignore
    return True
