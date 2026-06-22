"""In-memory metrics store with rolling endpoint aggregation."""

from __future__ import annotations

import threading
import time
from collections import defaultdict, deque
from collections.abc import Iterable

from .models import EndpointMetrics, RequestRecord


class MetricsStore:
    def __init__(self, window_seconds: float = 300.0, max_records: int = 10_000) -> None:
        self.window_seconds = window_seconds
        self._records: deque[RequestRecord] = deque(maxlen=max_records)
        self._lock = threading.RLock()

    def add(self, record: RequestRecord) -> None:
        with self._lock:
            self._records.append(record)
            self._trim_locked()

    def records(self) -> list[RequestRecord]:
        with self._lock:
            self._trim_locked()
            return list(self._records)

    def endpoints(self) -> list[str]:
        return sorted({record.endpoint for record in self.records()})

    def snapshot(self) -> dict[str, EndpointMetrics]:
        grouped: dict[str, list[RequestRecord]] = defaultdict(list)
        records = self.records()
        for record in records:
            grouped[record.endpoint].append(record)
        return {
            endpoint: aggregate_endpoint(endpoint, endpoint_records, self.window_seconds)
            for endpoint, endpoint_records in grouped.items()
        }

    def snapshot_by_host(self) -> dict[str, EndpointMetrics]:
        grouped: dict[str, list[RequestRecord]] = defaultdict(list)
        records = self.records()
        for record in records:
            host = record.host or "unknown"
            grouped[host].append(record)
        return {
            host: aggregate_endpoint(host, host_records, self.window_seconds) for host, host_records in grouped.items()
        }

    def snapshot_by_service(self) -> dict[str, EndpointMetrics]:
        grouped: dict[str, list[RequestRecord]] = defaultdict(list)
        records = self.records()
        for record in records:
            service = record.service or "unknown"
            grouped[service].append(record)
        return {
            service: aggregate_endpoint(service, service_records, self.window_seconds)
            for service, service_records in grouped.items()
        }

    def _trim_locked(self) -> None:
        cutoff = (time.time() - self.window_seconds) * 1000
        while self._records and self._records[0].timestamp_ms < cutoff:
            self._records.popleft()


def aggregate_endpoint(endpoint: str, records: Iterable[RequestRecord], window_seconds: float) -> EndpointMetrics:
    items = list(records)
    latencies = sorted(record.latency_ms for record in items)
    count = len(items)
    successes = sum(1 for record in items if record.ok)
    failures = count - successes

    status_2xx = sum(1 for r in items if r.status_code is not None and 200 <= r.status_code < 300)
    status_3xx = sum(1 for r in items if r.status_code is not None and 300 <= r.status_code < 400)
    status_4xx = sum(1 for r in items if r.status_code is not None and 400 <= r.status_code < 500)
    status_5xx = sum(1 for r in items if r.status_code is not None and 500 <= r.status_code < 600)

    return EndpointMetrics(
        endpoint=endpoint,
        count=count,
        success_count=successes,
        failure_count=failures,
        request_rate=count / window_seconds if window_seconds else 0.0,
        success_ratio=successes / count if count else 0.0,
        failure_ratio=failures / count if count else 0.0,
        p50_ms=percentile(latencies, 50),
        p95_ms=percentile(latencies, 95),
        p99_ms=percentile(latencies, 99),
        avg_ms=sum(latencies) / count if count else 0.0,
        status_2xx_count=status_2xx,
        status_3xx_count=status_3xx,
        status_4xx_count=status_4xx,
        status_5xx_count=status_5xx,
    )


def percentile(sorted_values: list[float], pct: float) -> float:
    if not sorted_values:
        return 0.0
    index = (len(sorted_values) - 1) * (pct / 100)
    lower = int(index)
    upper = min(lower + 1, len(sorted_values) - 1)
    weight = index - lower
    return sorted_values[lower] * (1 - weight) + sorted_values[upper] * weight
