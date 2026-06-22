"""Command line interface for pyNetScope."""

from __future__ import annotations

import argparse
import json
import time
import urllib.error
import urllib.request
from dataclasses import asdict
from typing import Any

from .core import NetScope, normalise_error
from .dashboard import LiveDashboard, render_dashboard
from .path import inspect_http_timing, inspect_path
from .prometheus import PrometheusMetricsServer
from .speedtest import SpeedtestTarget, run_speedtest
from .utils import to_plain


def load_config_and_env() -> dict[str, Any]:
    import os

    config: dict[str, Any] = {}

    if os.path.exists("pynetscope.toml"):
        try:
            import tomllib
        except ImportError:
            try:
                import tomli as tomllib
            except ImportError:
                tomllib = None
        if tomllib:
            try:
                with open("pynetscope.toml", "rb") as f:
                    config = tomllib.load(f)
            except Exception:
                pass

    for key, value in os.environ.items():
        if key.startswith("PYNETSCOPE_"):
            cfg_key = key[11:].lower()
            try:
                if "." in value:
                    config[cfg_key] = float(value)
                else:
                    config[cfg_key] = int(value)
            except ValueError:
                if value.lower() in ("true", "yes", "on"):
                    config[cfg_key] = True
                elif value.lower() in ("false", "no", "off"):
                    config[cfg_key] = False
                else:
                    config[cfg_key] = value
    return config


def main() -> int:
    parser = argparse.ArgumentParser(prog="pynetscope")
    subparsers = parser.add_subparsers(dest="command", required=True)

    speedtest_parser = subparsers.add_parser("speedtest", help="Run a network speed test")
    speedtest_parser.add_argument("--download-url", help="URL used for download throughput")
    speedtest_parser.add_argument("--upload-url", help="URL used for upload throughput")
    speedtest_parser.add_argument("--latency-url", help="URL used for latency probes")
    speedtest_parser.add_argument("--upload-bytes", type=int)
    speedtest_parser.add_argument("--timeout", type=float)
    speedtest_parser.add_argument("--json", action="store_true", help="Print machine-readable JSON")

    path_parser = subparsers.add_parser("path", help="Inspect DNS, TCP, and TLS timing")
    path_parser.add_argument("url")
    path_parser.add_argument("--timeout", type=float)
    path_parser.add_argument("--http", action="store_true", help="Include TTFB and total HTTP timing")
    path_parser.add_argument("--json", action="store_true", help="Print machine-readable JSON")

    watch_parser = subparsers.add_parser("watch", help="Watch an endpoint with repeated HTTP probes")
    watch_parser.add_argument("url")
    watch_parser.add_argument("--interval", type=float)
    watch_parser.add_argument("--count", type=int, help="Number of probes; 0 runs forever")
    watch_parser.add_argument("--timeout", type=float)
    watch_parser.add_argument("--json", action="store_true", help="Print final snapshot as JSON")
    watch_parser.add_argument("--plain", action="store_true", help="Disable Rich dashboard rendering")

    metrics_parser = subparsers.add_parser("serve-metrics", help="Serve a Prometheus /metrics endpoint")
    metrics_parser.add_argument("--host")
    metrics_parser.add_argument("--port", type=int)
    metrics_parser.add_argument("--watch", help="URL to watch in the background")
    metrics_parser.add_argument("--interval", type=float, default=2.0)

    snapshot_parser = subparsers.add_parser("snapshot", help="Capture a network snapshot")
    snapshot_parser.add_argument("url", nargs="?", help="URL to inspect")
    snapshot_parser.add_argument("--json", action="store_true", help="Print snapshot as JSON")

    args = parser.parse_args()

    # Apply configuration file and environment variables
    config = load_config_and_env()
    defaults = {
        "upload_bytes": 1_000_000,
        "timeout": 10.0,
        "interval": 2.0,
        "count": 0,
        "host": "127.0.0.1",
        "port": 8000,
    }
    for key in ["upload_bytes", "timeout", "interval", "count", "host", "port"]:
        val = getattr(args, key, None)
        if val is None:
            setattr(args, key, config.get(key, defaults[key]))

    if args.command == "speedtest":
        target = SpeedtestTarget(
            download_url=args.download_url or SpeedtestTarget.download_url,
            upload_url=args.upload_url or SpeedtestTarget.upload_url,
            latency_url=args.latency_url or SpeedtestTarget.latency_url,
        )
        speed_res = run_speedtest(
            target=target,
            upload_bytes=args.upload_bytes,
            timeout=args.timeout,
        )
        if args.json:
            print(json.dumps(asdict(speed_res), indent=2))
        else:
            print(f"Download: {speed_res.download_mbps:.2f} Mbps")
            print(f"Upload:   {speed_res.upload_mbps:.2f} Mbps")
            print(f"Latency:  {speed_res.latency_ms:.2f} ms")
            print(f"Target:   {speed_res.target_name}")
        return 0

    if args.command == "path":
        timeout_val = args.timeout if args.timeout is not None else 5.0
        path_res = (
            inspect_http_timing(args.url, timeout=timeout_val)
            if args.http
            else inspect_path(args.url, timeout=timeout_val)
        )
        if args.json:
            print(json.dumps(asdict(path_res), indent=2))
        else:
            print(f"Host: {path_res.host}:{path_res.port}")
            print(f"DNS:  {path_res.dns_ms:.2f} ms")
            print(f"TCP:  {path_res.tcp_ms:.2f} ms")
            if path_res.tls_ms is not None:
                print(f"TLS:  {path_res.tls_ms:.2f} ms")
            from .path import HTTPTiming

            if isinstance(path_res, HTTPTiming):
                print(f"TTFB: {path_res.ttfb_ms:.2f} ms")
                print(f"Total:{path_res.total_ms:.2f} ms")
                print(f"HTTP: {path_res.status_line}")
        return 0

    if args.command == "watch":
        scope = NetScope()
        try:
            import os

            from .exporters import SQLiteExporter
            db_path = "pynetscope.db"
            if os.path.exists(db_path):
                exporter = SQLiteExporter(db_path)
                past_records = exporter.read_records(limit=1000)
                for r in past_records:
                    scope.metrics.add(r)
        except Exception:
            pass

        completed = 0
        live = LiveDashboard() if not args.json and not args.plain else None
        try:
            if live and live.available:
                with live:
                    while args.count == 0 or completed < args.count:
                        _probe(scope, args.url, timeout=args.timeout)
                        completed += 1
                        live.update(scope.snapshot())
                        if args.count == 0 or completed < args.count:
                            time.sleep(args.interval)
            else:
                while args.count == 0 or completed < args.count:
                    _probe(scope, args.url, timeout=args.timeout)
                    completed += 1
                    snapshot = scope.snapshot()
                    if not args.json:
                        if args.plain or not render_dashboard(snapshot):
                            _print_watch(snapshot)
                    if args.count == 0 or completed < args.count:
                        time.sleep(args.interval)
        except KeyboardInterrupt:
            pass
        if args.json:
            print(json.dumps(to_plain(scope.snapshot()), indent=2))
        return 0

    if args.command == "serve-metrics":
        scope = NetScope()
        if args.watch:
            from threading import Thread

            def watch_loop():
                while True:
                    try:
                        _probe(scope, args.watch, timeout=args.timeout)
                    except Exception:
                        pass
                    time.sleep(args.interval)

            watch_thread = Thread(target=watch_loop, daemon=True)
            watch_thread.start()

        server = PrometheusMetricsServer(scope, host=args.host, port=args.port)
        host, port = server.address
        print(f"Serving Prometheus metrics on http://{host}:{port}/metrics")
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            server.shutdown()
        return 0

    if args.command == "snapshot":
        speedtest_result = run_speedtest()
        snapshot_data = {
            "timestamp": time.time() * 1000,
            "speedtest": asdict(speedtest_result),
        }
        if args.url:
            path_result = inspect_http_timing(args.url)
            snapshot_data["path_inspection"] = asdict(path_result)

        if args.json:
            print(json.dumps(snapshot_data, indent=2))
        else:
            print(f"Speedtest Target: {speedtest_result.target_name}")
            print(f"Download:         {speedtest_result.download_mbps:.2f} Mbps")
            print(f"Upload:           {speedtest_result.upload_mbps:.2f} Mbps")
            print(f"Latency:          {speedtest_result.latency_ms:.2f} ms")
            if args.url:
                print(f"Path:             {args.url}")
                print(f"DNS Timing:       {path_result.dns_ms:.2f} ms")
                print(f"TCP Timing:       {path_result.tcp_ms:.2f} ms")
        return 0

    return 2


def _probe(scope: NetScope, url: str, *, timeout: float) -> None:
    request = urllib.request.Request(url, method="GET", headers={"User-Agent": "pyNetScope/0.1 watch"})
    started = time.perf_counter()
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            response.read(1)
            latency_ms = (time.perf_counter() - started) * 1000
            scope.record("GET", url, response.status, latency_ms, response_headers=dict(response.headers))
    except urllib.error.HTTPError as exc:
        latency_ms = (time.perf_counter() - started) * 1000
        scope.record("GET", url, exc.code, latency_ms, error=normalise_error(exc), response_headers=dict(exc.headers))
    except Exception as exc:
        latency_ms = (time.perf_counter() - started) * 1000
        scope.record("GET", url, None, latency_ms, error=normalise_error(exc))


def _print_watch(snapshot: dict) -> None:
    metrics = snapshot["metrics"]
    health = snapshot["health"]
    print()
    print("Endpoint                                      Count  P95 ms  Success  Health")
    print("-" * 78)
    for endpoint, item in metrics.items():
        status = health[endpoint]
        print(
            f"{endpoint[:43]:43} "
            f"{item.count:5d} "
            f"{item.p95_ms:7.1f} "
            f"{item.success_ratio:7.0%} "
            f"{status.state}({status.score})"
        )


if __name__ == "__main__":
    raise SystemExit(main())
