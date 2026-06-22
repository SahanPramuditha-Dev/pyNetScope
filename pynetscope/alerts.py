"""Alert rules and sinks."""

from __future__ import annotations

import json
import time
import urllib.request
from collections.abc import Callable
from pathlib import Path
from typing import Protocol

from .models import Alert, EndpointMetrics
from .utils import to_plain


class AlertSink(Protocol):
    def emit(self, alert: Alert) -> None: ...


class ConsoleAlertSink:
    def emit(self, alert: Alert) -> None:
        print(f"[{alert.severity}] {alert.endpoint}: {alert.message}")


class FileAlertSink:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)

    def emit(self, alert: Alert) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(to_plain(alert)) + "\n")


class WebhookAlertSink:
    def __init__(self, url: str, timeout: float = 5.0) -> None:
        self.url = url
        self.timeout = timeout

    def emit(self, alert: Alert) -> None:
        payload = json.dumps(to_plain(alert)).encode("utf-8")
        request = urllib.request.Request(
            self.url,
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        urllib.request.urlopen(request, timeout=self.timeout).close()


class SlackAlertSink:
    def __init__(self, webhook_url: str, timeout: float = 5.0) -> None:
        self.webhook_url = webhook_url
        self.timeout = timeout

    def emit(self, alert: Alert) -> None:
        severity = getattr(alert, "severity", "warning").upper()
        icon = "🚨" if severity == "CRITICAL" else "⚠️"
        payload = {
            "text": (
                f"{icon} *[pyNetScope Alert - {severity}]*\n"
                f"*Rule:* `{alert.rule_name}`\n"
                f"*Endpoint:* `{alert.endpoint}`\n"
                f"*Message:* {alert.message}"
            )
        }
        data = json.dumps(payload).encode("utf-8")
        request = urllib.request.Request(
            self.webhook_url,
            data=data,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        urllib.request.urlopen(request, timeout=self.timeout).close()


class DiscordAlertSink:
    def __init__(self, webhook_url: str, timeout: float = 5.0) -> None:
        self.webhook_url = webhook_url
        self.timeout = timeout

    def emit(self, alert: Alert) -> None:
        severity = getattr(alert, "severity", "warning").upper()
        icon = "🚨" if severity == "CRITICAL" else "⚠️"
        payload = {
            "content": (
                f"{icon} **[pyNetScope Alert - {severity}]**\n"
                f"**Rule:** `{alert.rule_name}`\n"
                f"**Endpoint:** `{alert.endpoint}`\n"
                f"**Message:** {alert.message}"
            )
        }
        data = json.dumps(payload).encode("utf-8")
        request = urllib.request.Request(
            self.webhook_url,
            data=data,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        urllib.request.urlopen(request, timeout=self.timeout).close()


AlertPredicate = Callable[[EndpointMetrics], str | None]


class AlertEngine:
    def __init__(self, sinks: list[AlertSink] | None = None, cooldown_seconds: float = 60.0) -> None:
        from collections import deque

        self.sinks = sinks or [ConsoleAlertSink()]
        self.cooldown_seconds = cooldown_seconds
        self.rules: dict[str, AlertPredicate] = {}
        self._last_sent: dict[tuple[str, str], float] = {}
        self.recent_alerts: deque[Alert] = deque(maxlen=20)

    def add_latency_rule(self, threshold_ms: float, name: str = "latency-threshold") -> None:
        def predicate(metrics: EndpointMetrics) -> str | None:
            if metrics.p95_ms > threshold_ms:
                return f"p95 latency {metrics.p95_ms:.1f}ms exceeded {threshold_ms:.1f}ms"
            return None

        self.rules[name] = predicate

    def add_error_rate_rule(self, threshold: float, name: str = "error-rate") -> None:
        def predicate(metrics: EndpointMetrics) -> str | None:
            if metrics.failure_ratio > threshold:
                return f"failure ratio {metrics.failure_ratio:.0%} exceeded {threshold:.0%}"
            return None

        self.rules[name] = predicate

    def add_rule(self, name: str, predicate: AlertPredicate) -> None:
        self.rules[name] = predicate

    def evaluate(self, metrics: EndpointMetrics) -> list[Alert]:
        alerts = []
        now = time.time()
        for name, predicate in self.rules.items():
            message = predicate(metrics)
            key = (name, metrics.endpoint)
            if not message or now - self._last_sent.get(key, 0) < self.cooldown_seconds:
                continue
            alert = Alert(rule_name=name, endpoint=metrics.endpoint, message=message)
            self.recent_alerts.append(alert)
            for sink in self.sinks:
                try:
                    sink.emit(alert)
                except Exception:
                    pass
            self._last_sent[key] = now
            alerts.append(alert)
        return alerts
