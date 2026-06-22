# pyNetScope

[![CI](https://github.com/pyNetScope/pyNetScope/actions/workflows/ci.yml/badge.svg)](https://github.com/pyNetScope/pyNetScope/actions/workflows/ci.yml)
[![PyPI Version](https://img.shields.io/pypi/v/pynetscope.svg)](https://pypi.org/project/pynetscope/)
[![License](https://img.shields.io/github/license/pyNetScope/pyNetScope.svg)](LICENSE)

A lightweight, embedded network intelligence layer for Python applications:
zero-config observability for API calls, latency tracking, endpoint health
scoring, alerts, exports, path inspection, and speed tests.

pyNetScope is designed for developer-first SDK use. It can instrument
`requests` and `httpx` when those libraries are installed, but its core metrics,
health, alerts, exporters, tracing, path inspection, and speedtest modules use
the Python standard library.

## Install

```bash
pip install pynetscope
```

Optional extras:

```bash
pip install "pynetscope[requests]"
pip install "pynetscope[httpx]"
pip install "pynetscope[aiohttp]"
pip install "pynetscope[otel]"
pip install "pynetscope[rich]"
```

## Quick Start

### Auto Instrumentation

Automatically detect and safely instrument all supported libraries (`requests`, `httpx`, `aiohttp`, `urllib`):

```python
import pynetscope

pynetscope.install()

# Any calls via requests, httpx, aiohttp, or urllib.request are now instrumented.

pynetscope.uninstall()
```

### Scoped/Global Monkey Patching

```python
from pynetscope import NetScope

scope = NetScope()
handle = scope.instrument_requests()

# Existing requests calls are now observed.
handle.uninstall()
```

Context manager scoped:

```python
from pynetscope import NetScope

scope = NetScope()

with scope.instrumented_requests():
    # requests calls are observed only inside this block
    ...
```

### Session-Scoped Adapter Interceptor

If you want to avoid global monkey patching entirely, you can attach an HTTPAdapter to a specific `requests.Session` instance:

```python
import requests
from pynetscope import NetScope

scope = NetScope()
session = requests.Session()
scope.instrument_session(session)

# Only calls made via this session instance are tracked.
session.get("https://api.example.com/ok")
```

### Inbound Middleware (FastAPI, Flask, Django)

- **FastAPI / Starlette**:
  ```python
  from fastapi import FastAPI
  from pynetscope import instrument_fastapi_app
  
  app = FastAPI()
  instrument_fastapi_app(app)
  ```

- **Flask**:
  ```python
  from flask import Flask
  from pynetscope.flask import PyNetScopeFlaskMiddleware
  
  app = Flask(__name__)
  middleware = PyNetScopeFlaskMiddleware(app)
  ```

- **Django**:
  Add the middleware to your `MIDDLEWARE` list in `settings.py`:
  ```python
  MIDDLEWARE = [
      ...
      "pynetscope.django.PyNetScopeDjangoMiddleware",
  ]
  ```

### Manual Recording

```python
from pynetscope import NetScope

scope = NetScope()
scope.record("GET", "https://api.example.com/users/123", 200, 84.2)

snapshot = scope.snapshot()
print(snapshot["metrics"])
print(snapshot["health"])
```

## Core Engine

- `NetScope.instrument_requests()` monkey-patches `requests.Session.request`.
- `NetScope.instrument_httpx()` monkey-patches `httpx.Client.request`.
- `AsyncNetScope.instrument_httpx_async()` supports `httpx.AsyncClient`.
- `AsyncNetScope.instrument_aiohttp()` supports `aiohttp.ClientSession`.
- `NetScope.instrument_urllib()` monkey-patches `urllib.request.urlopen`.
- Instrumentation handles support idempotent calls and check if the replacement matches before reverting patches during uninstall.
- `NetScope.record()` accepts custom observations from any client. Supports registering background `flushing_hooks` run after record creation.
- Errors are normalised as `ErrorClass: message`.
- Trace headers are injected by default using W3C `traceparent` and `x-request-id`.

## Metrics Layer

`MetricsStore` keeps a thread-safe rolling window and aggregates per endpoint:

- request count and rate
- success and failure ratio
- rolling p50, p95, p99, and average latency
- status-code class counters (2xx, 3xx, 4xx, 5xx)
- custom regex-based configurable endpoint normalization rules
- snapshot metric grouping by host and service via `snapshot_by_host()` and `snapshot_by_service()`

## Health Engine

`HealthEngine` produces a 0-100 score with states:

- `Stable`
- `Degrading`
- `Critical`

The score accounts for failure ratio, p95 latency budget overages, timing/fingerprint window retry pattern detection, and baseline-window stddev anomaly detection.

## Alerts Engine

```python
from pynetscope import AlertEngine, ConsoleAlertSink, NetScope

alerts = AlertEngine(sinks=[ConsoleAlertSink()], cooldown_seconds=30)
alerts.add_latency_rule(500)
alerts.add_error_rate_rule(0.10)

scope = NetScope(alerts=alerts)
```

Built-in sinks:

- console
- file as NDJSON
- webhook as JSON POST
- custom callbacks via user-defined sinks

## Export Layer

```python
from pynetscope.exporters import export_json, export_prometheus, SQLiteExporter

snapshot = scope.snapshot()
print(export_json(snapshot["metrics"], snapshot["health"]))
print(export_prometheus(snapshot["metrics"]))

SQLiteExporter("pynetscope.db").write_records(snapshot["records"])
```

Features:
- CSV & NDJSON export support append mode (`append=True`).
- SQLiteExporter includes schema versioning/migrations and size retention pruning (`prune_records(before_timestamp_ms)`).

## Prometheus Server

```python
from pynetscope import NetScope, PrometheusMetricsServer

scope = NetScope()
server = PrometheusMetricsServer(scope, host="127.0.0.1", port=8000)
server.serve_forever()
```

Metrics are exposed at `/metrics` and include HTTP `method` labels.

## Network Path Inspector

```python
from pynetscope import inspect_path

path = inspect_path("https://example.com")
print(path.dns_ms, path.tcp_ms, path.tls_ms)
```

For TTFB and full transfer timing:

```python
from pynetscope import inspect_http_timing

timing = inspect_http_timing("https://example.com")
print(timing.ttfb_ms, timing.total_ms)
```

Measures:

- DNS lookup time
- TCP handshake latency
- TLS negotiation timing

## Speedtest

```python
from pynetscope import run_speedtest

result = run_speedtest()
print(result.download_mbps, result.upload_mbps, result.latency_ms)
print(result.min_latency_ms, result.max_latency_ms, result.jitter_ms, result.packet_failure_rate)
```

> [!WARNING]
> Running speedtests generates network traffic to public speedtest endpoints (e.g. Cloudflare, Tele2) and will expose your public IP address to those servers.

Supports warming up connections, custom download chunk sizes, duration-based test limits, latency stats (jitter, failure rate, min/max), server target validation, and parallel download streams.

## Circuit Breaker

```python
from pynetscope import CircuitBreaker

breaker = CircuitBreaker(failure_threshold=3, recovery_timeout=30)

if breaker.allow_request():
    try:
        # call upstream
        breaker.record_success()
    except Exception:
        breaker.record_failure()
```

Supports:

- closed, open, and half-open states
- configurable thresholds
- state change callback

## Plugin System

Plugins can implement `pre_request(context)` and/or `post_request(record)`.

```python
from pynetscope import PluginRegistry, NetScope

class MyPlugin:
    name = "my-plugin"

    def post_request(self, record):
        return record

plugins = PluginRegistry()
plugins.register(MyPlugin())

scope = NetScope(plugins=plugins)
```

Entry point loading uses the `pynetscope.plugins` group.

## OpenTelemetry Bridge

```python
from pynetscope import annotate_current_span, span_attributes
from pynetscope.otel import create_span

record = scope.record("GET", "https://api.example.com", 200, 42)
annotate_current_span(record)
create_span(record) # Creates a retrospective span matching exact record timings
```

`opentelemetry-api` is optional. If it is not installed, bridge functions return `False`. Call `setup_otlp_exporter(endpoint)` to configure SDK trace exporters.

## CLI & Configuration

```bash
pynetscope speedtest
pynetscope speedtest --json
pynetscope path https://example.com
pynetscope path https://example.com --http
pynetscope watch https://example.com --interval 2 --count 5
pynetscope serve-metrics --host 127.0.0.1 --port 8000 --watch https://example.com
pynetscope snapshot --json
```

### Config File & Env Variables
CLI parameters will automatically load defaults from a local `pynetscope.toml` configuration file if present in the working directory. In addition, any environment variables starting with `PYNETSCOPE_` (e.g., `PYNETSCOPE_TIMEOUT=5`) are automatically loaded.

### Terminal Dashboard
When `rich` is installed, `watch` renders a premium live interactive split-screen dashboard displaying:
- Real-Time Endpoint Metrics and latency history sparklines (` ▂▃▅█▆`).
- Live Alert Stream logging warnings/critical anomalies.

Use `--plain` to force the fallback table.

## Module Layout

```text
pynetscope/
  alerts.py
  async_support.py
  circuit_breaker.py
  cli.py
  core.py
  django.py
  exporters.py
  fastapi.py
  flask.py
  health.py
  instrumentation.py
  metrics.py
  models.py
  otel.py
  path.py
  plugins.py
  prometheus.py
  session.py
  speedtest.py
  tracing.py
  utils.py
```

## Status

This is a v0.1 MVP scaffold. The public API is intentionally small and the
implementation favours embeddability over heavyweight background services.
See `docs/api-stability.md` for compatibility rules.
#   t e s t  
 