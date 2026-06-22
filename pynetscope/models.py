"""Shared data models for pyNetScope."""

from __future__ import annotations

import time
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any


def now_ms() -> float:
    return time.time() * 1000


@dataclass(frozen=True)
class RequestContext:
    method: str
    url: str
    headers: Mapping[str, str] = field(default_factory=dict)
    started_at: float = field(default_factory=now_ms)
    trace_id: str | None = None
    metadata: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class RequestRecord:
    method: str
    url: str
    endpoint: str
    status_code: int | None
    latency_ms: float
    ok: bool
    error: str | None = None
    trace_id: str | None = None
    request_headers: Mapping[str, str] = field(default_factory=dict)
    response_headers: Mapping[str, str] = field(default_factory=dict)
    timestamp_ms: float = field(default_factory=now_ms)
    metadata: Mapping[str, Any] = field(default_factory=dict)
    host: str | None = None
    service: str | None = None


@dataclass(frozen=True)
class EndpointMetrics:
    endpoint: str
    count: int
    success_count: int
    failure_count: int
    request_rate: float
    success_ratio: float
    failure_ratio: float
    p50_ms: float
    p95_ms: float
    p99_ms: float
    avg_ms: float
    status_2xx_count: int = 0
    status_3xx_count: int = 0
    status_4xx_count: int = 0
    status_5xx_count: int = 0


@dataclass(frozen=True)
class HealthStatus:
    endpoint: str
    score: int
    state: str
    reasons: tuple[str, ...] = ()


@dataclass(frozen=True)
class Alert:
    rule_name: str
    endpoint: str
    message: str
    severity: str = "warning"
    timestamp_ms: float = field(default_factory=now_ms)
