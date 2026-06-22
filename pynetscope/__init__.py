"""pyNetScope public API."""

from ._version import __version__
from .alerts import AlertEngine, ConsoleAlertSink, DiscordAlertSink, FileAlertSink, SlackAlertSink, WebhookAlertSink
from .async_support import AsyncNetScope
from .circuit_breaker import CircuitBreaker
from .core import NetScope
from .db import instrument_sqlite
from .django import PyNetScopeDjangoMiddleware
from .fastapi import PyNetScopeMiddleware, instrument_fastapi_app
from .flask import PyNetScopeFlaskMiddleware
from .health import HealthEngine
from .instrumentation import InstrumentationHandle, install, uninstall
from .metrics import MetricsStore
from .models import Alert, EndpointMetrics, HealthStatus, RequestContext, RequestRecord
from .otel import annotate_current_span, span_attributes
from .path import HTTPTiming, PathInspection, inspect_http_timing, inspect_path
from .plugins import PluginRegistry
from .prometheus import PrometheusMetricsServer
from .session import NetScopeHTTPAdapter, inspect_session
from .speedtest import (
    DEFAULT_TARGETS,
    SpeedtestError,
    SpeedtestResult,
    SpeedtestTarget,
    choose_best_target,
    run_speedtest,
)
from .tracing import current_trace_id, ensure_trace_id, inject_headers, set_trace_id, traceparent
from .utils import endpoint_normalizer

__all__ = [
    "Alert",
    "AlertEngine",
    "AsyncNetScope",
    "CircuitBreaker",
    "ConsoleAlertSink",
    "DEFAULT_TARGETS",
    "EndpointMetrics",
    "FileAlertSink",
    "HealthEngine",
    "HealthStatus",
    "HTTPTiming",
    "InstrumentationHandle",
    "MetricsStore",
    "NetScope",
    "NetScopeHTTPAdapter",
    "inspect_session",
    "PathInspection",
    "PluginRegistry",
    "PrometheusMetricsServer",
    "PyNetScopeDjangoMiddleware",
    "PyNetScopeFlaskMiddleware",
    "PyNetScopeMiddleware",
    "RequestContext",
    "RequestRecord",
    "SpeedtestError",
    "SpeedtestResult",
    "SpeedtestTarget",
    "WebhookAlertSink",
    "SlackAlertSink",
    "DiscordAlertSink",
    "choose_best_target",
    "current_trace_id",
    "endpoint_normalizer",
    "ensure_trace_id",
    "annotate_current_span",
    "inject_headers",
    "inspect_http_timing",
    "inspect_path",
    "instrument_fastapi_app",
    "instrument_sqlite",
    "install",
    "uninstall",
    "run_speedtest",
    "set_trace_id",
    "span_attributes",
    "traceparent",
    "__version__",
]

