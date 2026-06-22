"""Endpoint health scoring."""

from __future__ import annotations

import math
import time

from .models import EndpointMetrics, HealthStatus, RequestRecord


class HealthEngine:
    def __init__(
        self,
        *,
        latency_budget_ms: float = 1_000.0,
        critical_error_ratio: float = 0.5,
        anomaly_threshold_stddev: float = 3.0,
        baseline_window_seconds: float = 3600.0,
        use_ema: bool = False,
        ema_alpha: float = 0.2,
        ema_multiplier: float = 2.0,
    ) -> None:
        self.latency_budget_ms = latency_budget_ms
        self.critical_error_ratio = critical_error_ratio
        self.anomaly_threshold_stddev = anomaly_threshold_stddev
        self.baseline_window_seconds = baseline_window_seconds
        self.use_ema = use_ema
        self.ema_alpha = ema_alpha
        self.ema_multiplier = ema_multiplier

    def score(self, metrics: EndpointMetrics, records: list[RequestRecord] | None = None) -> HealthStatus:
        score = 100
        reasons: list[str] = []

        if metrics.failure_ratio:
            penalty = int(metrics.failure_ratio * 70)
            score -= penalty
            reasons.append(f"failure ratio {metrics.failure_ratio:.0%}")

        if metrics.p95_ms > self.latency_budget_ms:
            overage = min(metrics.p95_ms / self.latency_budget_ms, 3.0)
            score -= int(15 * overage)
            reasons.append(f"p95 latency {metrics.p95_ms:.1f}ms")

        if records and has_retry_pattern(records):
            score -= 10
            reasons.append("retry pattern detected")

        if records:
            if self.use_ema:
                anomaly_reason = detect_ema_anomaly(
                    metrics.endpoint,
                    records,
                    alpha=self.ema_alpha,
                    multiplier=self.ema_multiplier,
                )
            else:
                anomaly_reason = detect_latency_anomaly(
                    metrics.endpoint,
                    records,
                    stddev_multiplier=self.anomaly_threshold_stddev,
                    baseline_window_ms=self.baseline_window_seconds * 1000.0,
                )
            if anomaly_reason:
                score -= 15
                reasons.append(anomaly_reason)

        score = max(0, min(100, score))
        if score < 40 or metrics.failure_ratio >= self.critical_error_ratio:
            state = "Critical"
        elif score < 75:
            state = "Degrading"
        else:
            state = "Stable"
        return HealthStatus(endpoint=metrics.endpoint, score=score, state=state, reasons=tuple(reasons))


def has_retry_pattern(records: list[RequestRecord], max_interval_ms: float = 2000.0) -> bool:
    from collections import defaultdict

    grouped = defaultdict(list)
    for record in records:
        grouped[record.endpoint].append(record)

    for _endpoint, group in grouped.items():
        if len(group) < 2:
            continue
        sorted_group = sorted(group, key=lambda r: r.timestamp_ms)
        for i in range(len(sorted_group) - 1):
            if not sorted_group[i].ok:
                time_diff = sorted_group[i + 1].timestamp_ms - sorted_group[i].timestamp_ms
                if 0 <= time_diff <= max_interval_ms:
                    return True
    return False


def detect_latency_anomaly(
    endpoint: str,
    records: list[RequestRecord],
    recent_window_ms: float = 60000.0,
    stddev_multiplier: float = 3.0,
    baseline_window_ms: float = 3600000.0,
) -> str | None:
    """Detect if recent latency is anomalous compared to baseline (older records)."""
    now_ms = time.time() * 1000
    cutoff = now_ms - recent_window_ms

    endpoint_records = [r for r in records if r.endpoint == endpoint]
    if len(endpoint_records) < 10:
        return None

    baseline = [r.latency_ms for r in endpoint_records if cutoff - baseline_window_ms <= r.timestamp_ms < cutoff]
    recent = [r.latency_ms for r in endpoint_records if r.timestamp_ms >= cutoff]

    if len(baseline) < 5 or not recent:
        return None

    mean = sum(baseline) / len(baseline)
    variance = sum((x - mean) ** 2 for x in baseline) / len(baseline)
    stddev = math.sqrt(variance)

    recent_avg = sum(recent) / len(recent)
    stddev = max(stddev, 5.0)

    if recent_avg > mean + stddev_multiplier * stddev:
        return (
            f"latency anomaly detected (recent avg {recent_avg:.1f}ms exceeds "
            f"baseline mean {mean:.1f}ms by > {stddev_multiplier}x stddev)"
        )
    return None


def detect_ema_anomaly(
    endpoint: str,
    records: list[RequestRecord],
    alpha: float = 0.2,
    multiplier: float = 2.0,
    recent_window_ms: float = 60000.0,
) -> str | None:
    """Detect if recent latency is anomalous compared to a rolling EMA baseline."""
    endpoint_records = [r for r in records if r.endpoint == endpoint]
    if len(endpoint_records) < 5:
        return None

    sorted_records = sorted(endpoint_records, key=lambda r: r.timestamp_ms)
    
    now_ms = time.time() * 1000
    recent_cutoff = now_ms - recent_window_ms
    
    baseline_records = [r for r in sorted_records if r.timestamp_ms < recent_cutoff]
    recent_records = [r for r in sorted_records if r.timestamp_ms >= recent_cutoff]
    
    if not baseline_records:
        baseline_records = sorted_records[:-1]
        recent_records = sorted_records[-1:]
        
    if not baseline_records or not recent_records:
        return None

    ema = baseline_records[0].latency_ms
    for r in baseline_records[1:]:
        ema = alpha * r.latency_ms + (1 - alpha) * ema

    recent_avg = sum(r.latency_ms for r in recent_records) / len(recent_records)

    if recent_avg > ema * multiplier:
        return (
            f"latency EMA anomaly detected (recent avg {recent_avg:.1f}ms exceeds "
            f"EMA {ema:.1f}ms by > {multiplier}x)"
        )
    return None
