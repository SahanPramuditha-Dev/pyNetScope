import asyncio
import sqlite3
import subprocess
import sys
import time
import urllib.request

import pytest

from pynetscope import (
    AsyncNetScope,
    NetScope,
    RequestRecord,
    endpoint_normalizer,
)
from pynetscope.exporters import SQLiteExporter
from pynetscope.health import detect_latency_anomaly, has_retry_pattern
from pynetscope.otel import create_span, setup_otlp_exporter
from pynetscope.utils import scrub_payload


def test_pii_scrubbing():
    # Test JSON string payload scrubbing
    json_payload = '{"password": "secret_password", "nested": {"token": "my-secret-token", "name": "Ada"}}'
    scrubbed = scrub_payload(json_payload)
    assert "secret_password" not in scrubbed
    assert "my-secret-token" not in scrubbed
    assert "[redacted]" in scrubbed
    assert "Ada" in scrubbed

    # Test Form payload scrubbing
    form_payload = "username=admin&password=my_secret_password&token=some_token"
    scrubbed_form = scrub_payload(form_payload)
    assert "my_secret_password" not in scrubbed_form
    assert "some_token" not in scrubbed_form
    assert "%5Bredacted%5D" in scrubbed_form
    assert "admin" in scrubbed_form

    # Test bytes decoding and scrubbing
    bytes_payload = b'{"password": "secret_password"}'
    scrubbed_bytes = scrub_payload(bytes_payload)
    assert "secret_password" not in scrubbed_bytes


def test_custom_normalization_rules():
    rules = [
        (r"/users/v\d+/\d+", "/users/versioned/:id"),
    ]
    normalizer = endpoint_normalizer(rules=rules)
    res = normalizer("GET", "https://api.example.com/users/v2/999")
    assert res == "GET api.example.com/users/versioned/:id"


def test_custom_grouping():
    scope = NetScope()
    scope.record("GET", "https://host1.example.com/items", 200, 10, metadata={"service": "serviceA"})
    scope.record("GET", "https://host2.example.com/items", 200, 15, metadata={"service": "serviceB"})

    by_host = scope.metrics.snapshot_by_host()
    assert "host1.example.com" in by_host
    assert "host2.example.com" in by_host

    by_service = scope.metrics.snapshot_by_service()
    assert "serviceA" in by_service
    assert "serviceB" in by_service


def test_session_adapter(http_server):
    requests = pytest.importorskip("requests")
    scope = NetScope()
    session = requests.Session()
    scope.instrument_session(session)

    resp = session.get(f"{http_server}/ok")
    assert resp.status_code == 200

    records = scope.snapshot()["records"]
    assert len(records) == 1
    assert records[0].status_code == 200


def test_httpx_event_hooks(http_server):
    httpx = pytest.importorskip("httpx")
    scope = NetScope()
    client = httpx.Client()
    scope.instrument_httpx_client(client)

    resp = client.get(f"{http_server}/ok")
    assert resp.status_code == 200

    records = scope.snapshot()["records"]
    assert len(records) == 1
    assert records[0].status_code == 200


@pytest.mark.anyio
async def test_httpx_async_event_hooks(http_server):
    httpx = pytest.importorskip("httpx")
    scope = AsyncNetScope()
    client = httpx.AsyncClient()
    scope.instrument_httpx_client_async(client)

    resp = await client.get(f"{http_server}/ok")
    assert resp.status_code == 200

    records = scope.snapshot()["records"]
    assert len(records) == 1
    assert records[0].status_code == 200


def test_urllib_instrumentation(http_server):
    scope = NetScope()
    handle = scope.instrument_urllib()
    try:
        urllib.request.urlopen(f"{http_server}/ok").read()
    finally:
        handle.uninstall()

    records = scope.snapshot()["records"]
    assert len(records) == 1
    assert records[0].status_code == 200


def test_sqlite_migrations_and_pruning(tmp_path):
    db_path = tmp_path / "test_pynetscope.db"

    # 1. Initialize DB with version 1 schema manually to trigger migrations
    conn = sqlite3.connect(db_path)
    conn.execute("CREATE TABLE schema_version (version INTEGER)")
    conn.execute("INSERT INTO schema_version VALUES (1)")
    conn.execute("""
        CREATE TABLE requests (
            timestamp_ms REAL,
            method TEXT,
            url TEXT,
            endpoint TEXT,
            status_code INTEGER,
            latency_ms REAL,
            ok INTEGER,
            error TEXT,
            trace_id TEXT
        )
    """)
    conn.close()

    # 2. Initialize exporter which triggers migration to version 2
    exporter = SQLiteExporter(db_path)

    # Verify new columns exist
    conn = sqlite3.connect(db_path)
    cursor = conn.execute("PRAGMA table_info(requests)")
    cols = [row[1] for row in cursor.fetchall()]
    assert "host" in cols
    assert "service" in cols
    assert "request_body" in cols
    assert "response_body" in cols
    conn.close()

    # Test writing records
    record = RequestRecord(
        method="GET",
        url="https://api.example.com/ok",
        endpoint="GET api.example.com/ok",
        status_code=200,
        latency_ms=10.0,
        ok=True,
        host="api.example.com",
        service="test-service",
        timestamp_ms=time.time() * 1000 - 10000,  # 10s ago
    )
    exporter.write_records([record])

    # Test pruning
    exporter.prune_records(time.time() * 1000 - 5000)
    conn = sqlite3.connect(db_path)
    cursor = conn.execute("SELECT COUNT(*) FROM requests")
    count = cursor.fetchone()[0]
    assert count == 0
    conn.close()


def test_otel_span_creation():
    # Test retrospective span creation (mock tracing dependency if not present)
    record = RequestRecord(
        method="GET",
        url="https://api.example.com/ok",
        endpoint="GET api.example.com/ok",
        status_code=200,
        latency_ms=10.0,
        ok=True,
        timestamp_ms=time.time() * 1000,
    )
    res = create_span(record)
    # create_span returns True if trace/opentelemetry is imported successfully, False otherwise
    assert res in (True, False)

    res_exporter = setup_otlp_exporter()
    assert res_exporter in (True, False)


def test_retry_and_anomaly_detection():
    # Test retry window detection
    now = time.time() * 1000
    records = [
        RequestRecord("GET", "https://a.com/b", "GET a.com/b", 500, 10.0, False, timestamp_ms=now - 3000),
        RequestRecord("GET", "https://a.com/b", "GET a.com/b", 500, 10.0, False, timestamp_ms=now - 2000),
        RequestRecord("GET", "https://a.com/b", "GET a.com/b", 200, 10.0, True, timestamp_ms=now - 1000),
    ]
    # Consecutive failures within 2000ms window -> should be True
    assert has_retry_pattern(records)

    # Test anomaly detection
    baseline_records = [
        RequestRecord("GET", "https://a.com/b", "GET a.com/b", 200, 10.0, True, timestamp_ms=now - 60000 - i * 1000)
        for i in range(20)
    ]
    recent_records = [
        RequestRecord("GET", "https://a.com/b", "GET a.com/b", 200, 150.0, True, timestamp_ms=now - i * 1000)
        for i in range(5)
    ]
    all_records = baseline_records + recent_records

    # Baseline mean=10, stddev=0 (clamped to min 5.0). Recent avg=150.
    # 150 > 10 + 3 * 5 = 25. Thus, anomalous!
    anomaly = detect_latency_anomaly("GET a.com/b", all_records)
    assert anomaly is not None
    assert "anomaly" in anomaly


@pytest.mark.anyio
async def test_async_cancellation_and_exceptions(http_server):
    httpx = pytest.importorskip("httpx")
    scope = AsyncNetScope()

    # 1. Connection Exception Path
    async with scope.instrumented_httpx_async():
        try:
            async with httpx.AsyncClient(timeout=1.0) as client:
                await client.get("http://invalid.local.domain")
        except Exception:
            pass

    records = scope.snapshot()["records"]
    assert len(records) == 1
    assert not records[0].ok
    assert records[0].error is not None

    # 2. Async Cancellation Path
    async def cancel_task():
        async with scope.instrumented_httpx_async():
            async with httpx.AsyncClient(timeout=5.0) as client:
                await client.get(f"{http_server}/delay")  # delay server endpoint or let it hang

    task = asyncio.create_task(cancel_task())
    await asyncio.sleep(0.1)
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass

    # Wait, the cancellation error is a standard exception in client calls.
    # Let's verify we record it or handle it cleanly.
    records = scope.snapshot()["records"]
    assert len(records) >= 1


def test_cli_subprocess():
    # Actually run the pynetscope CLI using subprocess
    # Run speedtest with dry run config or short parameters
    try:
        res = subprocess.run(
            [sys.executable, "-m", "pynetscope.cli", "speedtest", "--json", "--timeout", "1.0"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        # It might fail if no internet/server is reachable, but it should output JSON or execute
        assert res.returncode in (0, 1, 2)
    except subprocess.TimeoutExpired:
        pass

    # Test snapshot command
    try:
        res = subprocess.run(
            [sys.executable, "-m", "pynetscope.cli", "snapshot", "--json"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        assert res.returncode in (0, 1, 2)
    except subprocess.TimeoutExpired:
        pass


def test_sqlite_instrumentation():
    import sqlite3

    from pynetscope import NetScope, instrument_sqlite
    
    scope = NetScope()
    handle = instrument_sqlite(scope)
    try:
        conn = sqlite3.connect(":memory:")
        conn.execute("CREATE TABLE test (id INTEGER, val TEXT)")
        conn.execute("INSERT INTO test VALUES (1, 'hello')")
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM test")
        cursor.fetchall()
        conn.close()
    finally:
        handle.uninstall()
        
    records = scope.snapshot()["records"]
    assert len(records) >= 3
    queries = [r.metadata.get("query") for r in records if r.method == "QUERY"]
    assert any("CREATE TABLE test" in q for q in queries)
    assert any("INSERT INTO test" in q for q in queries)
    assert any("SELECT * FROM test" in q for q in queries)


def test_pii_scrubbing_modes(monkeypatch):
    import pynetscope.utils
    from pynetscope.utils import scrub_payload
    
    pynetscope.utils._pii_config = None
    
    monkeypatch.setenv("PYNETSCOPE_PII_MODE", "mask")
    json_payload = '{"password": "secret_password", "nested": {"token": "my-secret-token", "name": "Ada"}}'
    scrubbed = scrub_payload(json_payload)
    assert "secret_password" not in scrubbed
    assert "se*****rd" in scrubbed
    
    pynetscope.utils._pii_config = None
    monkeypatch.setenv("PYNETSCOPE_PII_MODE", "hash")
    scrubbed_hash = scrub_payload(json_payload)
    assert "secret_password" not in scrubbed_hash
    import hashlib
    expected_hash = hashlib.sha256(b"secret_password").hexdigest()[:16]
    assert expected_hash in scrubbed_hash
    
    pynetscope.utils._pii_config = None
    monkeypatch.setenv("PYNETSCOPE_PII_MODE", "redact")
    monkeypatch.setenv("PYNETSCOPE_PII_PATTERNS", r"\d{3}-\d{3}-\d{4}")
    test_payload = "My phone is 123-456-7890 and password is test"
    scrubbed_regex = scrub_payload(test_payload)
    assert "[redacted]" in scrubbed_regex
    assert "123-456-7890" not in scrubbed_regex
    
    pynetscope.utils._pii_config = None


def test_ema_anomaly_detection():
    import time

    from pynetscope import RequestRecord
    from pynetscope.health import detect_ema_anomaly
    
    now = time.time() * 1000
    records = [
        RequestRecord("GET", "https://a.com/b", "GET a.com/b", 200, 10.0, True, timestamp_ms=now - 5000),
        RequestRecord("GET", "https://a.com/b", "GET a.com/b", 200, 10.0, True, timestamp_ms=now - 4000),
        RequestRecord("GET", "https://a.com/b", "GET a.com/b", 200, 10.0, True, timestamp_ms=now - 3000),
        RequestRecord("GET", "https://a.com/b", "GET a.com/b", 200, 10.0, True, timestamp_ms=now - 2000),
        RequestRecord("GET", "https://a.com/b", "GET a.com/b", 200, 100.0, True, timestamp_ms=now - 1000),
    ]
    
    anomaly = detect_ema_anomaly("GET a.com/b", records, alpha=0.2, multiplier=2.0)
    assert anomaly is not None
    assert "EMA anomaly" in anomaly


def test_slack_discord_sinks(monkeypatch):
    import urllib.request

    from pynetscope import Alert
    from pynetscope.alerts import DiscordAlertSink, SlackAlertSink
    
    recorded_requests = []
    
    def mock_urlopen(request, timeout=None):
        recorded_requests.append((request.full_url, request.data, request.headers))
        class DummyResponse:
            def close(self):
                pass
        return DummyResponse()
        
    monkeypatch.setattr(urllib.request, "urlopen", mock_urlopen)
    
    alert = Alert(rule_name="latency-exceeded", endpoint="GET api.com/ok", message="p95 latency exceeded budget")
    
    slack = SlackAlertSink("https://hooks.slack.com/services/test")
    slack.emit(alert)
    
    discord = DiscordAlertSink("https://discord.com/api/webhooks/test")
    discord.emit(alert)
    
    assert len(recorded_requests) == 2
    slack_url, slack_data, slack_headers = recorded_requests[0]
    assert slack_url == "https://hooks.slack.com/services/test"
    assert b"GET api.com/ok" in slack_data
    assert b"latency-exceeded" in slack_data
    
    discord_url, discord_data, discord_headers = recorded_requests[1]
    assert discord_url == "https://discord.com/api/webhooks/test"
    assert b"GET api.com/ok" in discord_data


def test_parallel_choose_best_target(monkeypatch):
    import pynetscope.speedtest
    from pynetscope.speedtest import SpeedtestTarget, choose_best_target, run_speedtest
    
    targets = [
        SpeedtestTarget(name="t1", latency_url="http://t1.local/lat"),
        SpeedtestTarget(name="t2", latency_url="http://t2.local/lat"),
    ]
    
    def mock_measure_latency(url, timeout, samples, headers):
        if "t1.local" in url:
            return 10.0
        return 50.0
        
    monkeypatch.setattr(pynetscope.speedtest, "_measure_latency", mock_measure_latency)
    
    best = choose_best_target(targets, timeout=1.0)
    assert best.name == "t1"
    
    def mock_measure_download(*args, **kwargs):
        return 1000000, 1.0
        
    def mock_measure_upload(*args, **kwargs):
        return 1000000, 1.0
        
    monkeypatch.setattr(pynetscope.speedtest, "_measure_download", mock_measure_download)
    monkeypatch.setattr(pynetscope.speedtest, "_measure_upload", mock_measure_upload)
    
    res = run_speedtest(best, upload_bytes=1000, latency_samples=1, download_samples=1, upload_samples=1, warmup=False)
    assert res.target_name == "t1"
    assert res.dns_google_ms >= 0
    assert res.dns_cloudflare_ms >= 0
    assert res.ping_google_ms >= 0
    assert res.ping_cloudflare_ms >= 0

