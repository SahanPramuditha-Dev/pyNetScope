import asyncio
import urllib.request
from concurrent.futures import ThreadPoolExecutor

import pytest

from pynetscope import AsyncNetScope, NetScope, PrometheusMetricsServer
from pynetscope.exporters import SQLiteExporter, export_csv, export_ndjson


def test_requests_instrumentation_records_and_uninstalls(http_server):
    requests = pytest.importorskip("requests")
    scope = NetScope()
    original = requests.sessions.Session.request

    handle = scope.instrument_requests()
    try:
        response = requests.get(f"{http_server}/ok", timeout=5)
    finally:
        handle.uninstall()

    assert response.status_code == 200
    assert requests.sessions.Session.request is original
    assert len(scope.snapshot()["records"]) == 1


def test_requests_context_manager_uninstalls(http_server):
    requests = pytest.importorskip("requests")
    scope = NetScope()
    original = requests.sessions.Session.request

    with scope.instrumented_requests():
        requests.get(f"{http_server}/ok", timeout=5)

    assert requests.sessions.Session.request is original


def test_httpx_instrumentation_records_and_uninstalls(http_server):
    httpx = pytest.importorskip("httpx")
    scope = NetScope()
    original = httpx.Client.request

    handle = scope.instrument_httpx()
    try:
        response = httpx.get(f"{http_server}/ok", timeout=5)
    finally:
        handle.uninstall()

    assert response.status_code == 200
    assert httpx.Client.request is original
    assert len(scope.snapshot()["records"]) == 1


def test_httpx_async_instrumentation_records_and_uninstalls(http_server):
    httpx = pytest.importorskip("httpx")
    scope = AsyncNetScope()
    original = httpx.AsyncClient.request

    async def run():
        handle = scope.instrument_httpx_async()
        try:
            async with httpx.AsyncClient(timeout=5) as client:
                response = await client.get(f"{http_server}/ok")
        finally:
            handle.uninstall()
        return response

    response = asyncio.run(run())
    assert response.status_code == 200
    assert httpx.AsyncClient.request is original
    assert len(scope.snapshot()["records"]) == 1


def test_aiohttp_instrumentation_records_and_uninstalls(http_server):
    aiohttp = pytest.importorskip("aiohttp")
    scope = AsyncNetScope()
    original = aiohttp.ClientSession._request

    async def run():
        handle = scope.instrument_aiohttp()
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(f"{http_server}/ok") as response:
                    await response.text()
        finally:
            handle.uninstall()
        return response.status

    status = asyncio.run(run())
    assert status == 200
    assert aiohttp.ClientSession._request is original
    assert len(scope.snapshot()["records"]) == 1


def test_prometheus_metrics_server_exposes_metrics():
    scope = NetScope()
    scope.record("GET", "https://api.example.com/items", 200, 10)
    server = PrometheusMetricsServer(scope, port=0).start_background()
    host, port = server.address
    try:
        with urllib.request.urlopen(f"http://{host}:{port}/metrics", timeout=5) as response:
            payload = response.read().decode("utf-8")
    finally:
        server.shutdown()

    assert "pynetscope_requests_total" in payload
    assert "GET api.example.com/items" in payload


def test_file_exporters_write_outputs(tmp_path):
    scope = NetScope()
    scope.record("GET", "https://api.example.com/items", 200, 10)
    records = scope.snapshot()["records"]

    csv_path = tmp_path / "records.csv"
    ndjson_path = tmp_path / "records.ndjson"
    sqlite_path = tmp_path / "records.sqlite"

    export_csv(records, csv_path)
    export_ndjson(records, ndjson_path)
    SQLiteExporter(sqlite_path).write_records(records)

    assert csv_path.read_text(encoding="utf-8").startswith("method,url,endpoint")
    assert "api.example.com" in ndjson_path.read_text(encoding="utf-8")
    assert sqlite_path.exists()


def test_metrics_store_handles_concurrent_records():
    scope = NetScope()

    def record(index):
        scope.record("GET", f"https://api.example.com/items/{index}", 200, float(index % 50))

    with ThreadPoolExecutor(max_workers=8) as executor:
        list(executor.map(record, range(250)))

    snapshot = scope.snapshot()
    total = sum(metrics.count for metrics in snapshot["metrics"].values())
    assert total == 250
