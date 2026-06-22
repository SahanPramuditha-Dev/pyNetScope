import json

from pynetscope.exporters import export_json, export_prometheus
from pynetscope.health import HealthEngine
from pynetscope.metrics import MetricsStore
from pynetscope.models import RequestRecord


def test_metrics_percentiles_and_health_score():
    store = MetricsStore(window_seconds=60)
    endpoint = "GET api.example.com/items"
    for latency in [10, 20, 30, 40, 500]:
        store.add(RequestRecord("GET", "https://api.example.com/items", endpoint, 200, latency, True))
    store.add(RequestRecord("GET", "https://api.example.com/items", endpoint, 500, 600, False))

    metrics = store.snapshot()[endpoint]
    health = HealthEngine(latency_budget_ms=100).score(metrics, store.records())

    assert metrics.count == 6
    assert metrics.failure_count == 1
    assert metrics.p50_ms == 35
    assert health.state in {"Degrading", "Critical"}


def test_json_and_prometheus_exporters():
    store = MetricsStore()
    store.add(RequestRecord("GET", "https://api.example.com", "GET api.example.com/", 200, 25.0, True))
    metrics = store.snapshot()

    parsed = json.loads(export_json(metrics))
    prometheus = export_prometheus(metrics)

    assert "metrics" in parsed
    assert "pynetscope_requests_total" in prometheus
    assert 'endpoint="GET api.example.com/"' in prometheus
