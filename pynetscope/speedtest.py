"""Network speed testing primitives.

The module intentionally uses only the Python standard library. It provides a
small embedded benchmark suitable for health checks and developer diagnostics,
not a replacement for full ISP-grade measurement suites.
"""

from __future__ import annotations

import os
import random
import socket
import struct
import time
import urllib.error
import urllib.request
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from statistics import median

DEFAULT_USER_AGENT = "pyNetScope/0.1 speedtest"


@dataclass(frozen=True)
class SpeedtestTarget:
    """HTTP endpoints used by a speed test run."""

    name: str = "cloudflare"
    download_url: str = "https://speed.cloudflare.com/__down?bytes=10000000"
    upload_url: str = "https://speed.cloudflare.com/__up"
    latency_url: str = "https://speed.cloudflare.com/__down?bytes=1"


@dataclass(frozen=True)
class SpeedtestResult:
    """Measured latency and throughput values."""

    target_name: str
    latency_ms: float
    download_mbps: float
    upload_mbps: float
    downloaded_bytes: int
    uploaded_bytes: int
    download_seconds: float
    upload_seconds: float
    download_samples: tuple[float, ...] = ()
    upload_samples: tuple[float, ...] = ()
    min_latency_ms: float = 0.0
    max_latency_ms: float = 0.0
    jitter_ms: float = 0.0
    packet_failure_rate: float = 0.0
    dns_google_ms: float = 0.0
    dns_cloudflare_ms: float = 0.0
    ping_google_ms: float = 0.0
    ping_cloudflare_ms: float = 0.0


class SpeedtestError(RuntimeError):
    """Raised when a speed test probe cannot complete."""


DEFAULT_TARGETS = (
    SpeedtestTarget(
        name="cloudflare",
        download_url="https://speed.cloudflare.com/__down?bytes=10000000",
        upload_url="https://speed.cloudflare.com/__up",
        latency_url="https://speed.cloudflare.com/__down?bytes=1",
    ),
    SpeedtestTarget(
        name="tele2",
        download_url="http://speedtest.tele2.net/10MB.zip",
        upload_url="http://speedtest.tele2.net/upload.php",
        latency_url="http://speedtest.tele2.net/",
    ),
)


def run_speedtest(
    target: SpeedtestTarget | None = None,
    *,
    targets: Iterable[SpeedtestTarget] | None = None,
    upload_bytes: int = 1_000_000,
    download_bytes: int | None = None,
    duration_seconds: float | None = None,
    timeout: float = 10.0,
    latency_samples: int = 3,
    download_samples: int = 2,
    upload_samples: int = 2,
    headers: Mapping[str, str] | None = None,
    warmup: bool = True,
    parallel_streams: int = 1,
) -> SpeedtestResult:
    """Run latency, download, and upload probes.

    Args:
        target: Endpoint set to test against. Defaults to the lowest-latency
            target from the built-in list.
        targets: Candidate target list used when target is not set.
        upload_bytes: Number of random bytes to send during the upload probe.
        download_bytes: Custom size of download chunk to fetch.
        duration_seconds: Max seconds to run download throughput tests.
        timeout: Per-request timeout in seconds.
        latency_samples: Number of latency probes; the median is reported.
        download_samples: Number of download throughput probes.
        upload_samples: Number of upload throughput probes.
        headers: Optional HTTP headers to include in every request.
        warmup: Perform warmup probe before measurement.
        parallel_streams: Number of concurrent download streams to run.

    Returns:
        A :class:`SpeedtestResult` with Mbps and timing values.
    """

    if upload_bytes <= 0:
        raise ValueError("upload_bytes must be greater than zero")
    if timeout <= 0:
        raise ValueError("timeout must be greater than zero")
    if latency_samples <= 0:
        raise ValueError("latency_samples must be greater than zero")
    if download_samples <= 0:
        raise ValueError("download_samples must be greater than zero")
    if upload_samples <= 0:
        raise ValueError("upload_samples must be greater than zero")

    request_headers = {"User-Agent": DEFAULT_USER_AGENT, **dict(headers or {})}
    selected_target = target or choose_best_target(
        tuple(targets or DEFAULT_TARGETS),
        timeout=timeout,
        latency_samples=latency_samples,
        headers=request_headers,
    )

    if warmup:
        try:
            _measure_latency(selected_target.latency_url, timeout=timeout, samples=1, headers=request_headers)
        except Exception:
            pass

    # Measure latency samples individually to calculate stats
    latency_samples_list = []
    failed_probes = 0
    for _ in range(latency_samples):
        try:
            lat = _measure_latency(selected_target.latency_url, timeout=timeout, samples=1, headers=request_headers)
            latency_samples_list.append(lat)
        except Exception:
            failed_probes += 1

    if not latency_samples_list:
        raise SpeedtestError(f"All {latency_samples} latency probes failed")

    latency_ms = median(latency_samples_list)
    min_latency = min(latency_samples_list)
    max_latency = max(latency_samples_list)
    if len(latency_samples_list) > 1:
        jitter = sum(
            abs(latency_samples_list[i] - latency_samples_list[i - 1]) for i in range(1, len(latency_samples_list))
        ) / (len(latency_samples_list) - 1)
    else:
        jitter = 0.0
    packet_failure = failed_probes / latency_samples

    # Perform download runs (potentially in parallel)
    if parallel_streams > 1:
        from concurrent.futures import ThreadPoolExecutor

        with ThreadPoolExecutor(max_workers=parallel_streams) as executor:
            download_runs = list(
                executor.map(
                    lambda _: _measure_download(
                        selected_target.download_url,
                        timeout=timeout,
                        headers=request_headers,
                        download_bytes=download_bytes,
                        duration_seconds=duration_seconds,
                    ),
                    range(download_samples),
                )
            )
    else:
        download_runs = [
            _measure_download(
                selected_target.download_url,
                timeout=timeout,
                headers=request_headers,
                download_bytes=download_bytes,
                duration_seconds=duration_seconds,
            )
            for _ in range(download_samples)
        ]

    upload_runs = [
        _measure_upload(
            selected_target.upload_url,
            upload_bytes=upload_bytes,
            timeout=timeout,
            headers=request_headers,
        )
        for _ in range(upload_samples)
    ]

    download_mbps_samples = tuple(_mbps(byte_count, seconds) for byte_count, seconds in download_runs)
    upload_mbps_samples = tuple(_mbps(byte_count, seconds) for byte_count, seconds in upload_runs)
    downloaded_bytes = sum(byte_count for byte_count, _ in download_runs)
    uploaded_bytes = sum(byte_count for byte_count, _ in upload_runs)
    download_seconds = sum(seconds for _, seconds in download_runs)
    upload_seconds = sum(seconds for _, seconds in upload_runs)

    if selected_target.name == "local":
        dns_google_ms = 0.0
        dns_cloudflare_ms = 0.0
        ping_google_ms = 0.0
        ping_cloudflare_ms = 0.0
    else:
        dns_google_ms = measure_dns_resolver_latency("8.8.8.8", timeout=min(timeout, 2.0))
        dns_cloudflare_ms = measure_dns_resolver_latency("1.1.1.1", timeout=min(timeout, 2.0))
        ping_google_ms = measure_tcp_ping("8.8.8.8", timeout=min(timeout, 2.0))
        ping_cloudflare_ms = measure_tcp_ping("1.1.1.1", timeout=min(timeout, 2.0))

    return SpeedtestResult(
        target_name=selected_target.name,
        latency_ms=latency_ms,
        download_mbps=median(download_mbps_samples),
        upload_mbps=median(upload_mbps_samples),
        downloaded_bytes=downloaded_bytes,
        uploaded_bytes=uploaded_bytes,
        download_seconds=download_seconds,
        upload_seconds=upload_seconds,
        download_samples=download_mbps_samples,
        upload_samples=upload_mbps_samples,
        min_latency_ms=min_latency,
        max_latency_ms=max_latency,
        jitter_ms=jitter,
        packet_failure_rate=packet_failure,
        dns_google_ms=dns_google_ms,
        dns_cloudflare_ms=dns_cloudflare_ms,
        ping_google_ms=ping_google_ms,
        ping_cloudflare_ms=ping_cloudflare_ms,
    )


def measure_dns_resolver_latency(dns_server: str, host_to_resolve: str = "example.com", timeout: float = 2.0) -> float:
    tx_id = random.randint(0, 65535)
    header = struct.pack("!HHHHHH", tx_id, 0x0100, 1, 0, 0, 0)
    qname = b""
    for part in host_to_resolve.split("."):
        qname += struct.pack("B", len(part)) + part.encode("ascii")
    qname += b"\x00"
    question = qname + struct.pack("!HH", 1, 1)
    packet = header + question
    
    started = time.perf_counter()
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.settimeout(timeout)
        sock.sendto(packet, (dns_server, 53))
        data, _ = sock.recvfrom(512)
        latency = (time.perf_counter() - started) * 1000
        if len(data) >= 2:
            resp_id = struct.unpack("!H", data[:2])[0]
            if resp_id == tx_id:
                return latency
        return latency
    except Exception:
        return 0.0


def measure_tcp_ping(host: str, port: int = 53, timeout: float = 2.0) -> float:
    started = time.perf_counter()
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(timeout)
        sock.connect((host, port))
        sock.close()
        return (time.perf_counter() - started) * 1000
    except Exception:
        return 0.0


def choose_best_target(
    targets: Iterable[SpeedtestTarget],
    *,
    timeout: float = 5.0,
    latency_samples: int = 2,
    headers: Mapping[str, str] | None = None,
) -> SpeedtestTarget:
    candidates = tuple(targets)
    if not candidates:
        raise ValueError("targets must contain at least one SpeedtestTarget")

    request_headers = {"User-Agent": DEFAULT_USER_AGENT, **dict(headers or {})}
    
    from concurrent.futures import ThreadPoolExecutor
    from urllib.parse import urlsplit

    results: list[tuple[float, SpeedtestTarget]] = []
    failures: list[str] = []

    def check_target(candidate: SpeedtestTarget) -> tuple[float | None, SpeedtestTarget, str | None]:
        parts = urlsplit(candidate.latency_url)
        if not parts.scheme or not parts.netloc:
            return None, candidate, f"Invalid URL '{candidate.latency_url}'"
        try:
            latency = _measure_latency(
                candidate.latency_url,
                timeout=timeout,
                samples=latency_samples,
                headers=request_headers,
            )
            return latency, candidate, None
        except SpeedtestError as exc:
            return None, candidate, str(exc)

    with ThreadPoolExecutor(max_workers=len(candidates)) as executor:
        futures_results = list(executor.map(check_target, candidates))

    for latency, candidate, error_msg in futures_results:
        if latency is not None:
            results.append((latency, candidate))
        else:
            failures.append(f"{candidate.name}: {error_msg}")

    if not results:
        details = "; ".join(failures) if failures else "no candidates"
        raise SpeedtestError(f"no speedtest target was reachable: {details}")
    return min(results, key=lambda item: item[0])[1]


def _measure_latency(
    url: str,
    *,
    timeout: float,
    samples: int,
    headers: Mapping[str, str],
) -> float:
    measurements = []
    for _ in range(samples):
        request = urllib.request.Request(url, headers=dict(headers), method="GET")
        started_at = time.perf_counter()
        _open(request, timeout=timeout).close()
        measurements.append((time.perf_counter() - started_at) * 1000)
    return median(measurements)


def _measure_download(
    url: str,
    *,
    timeout: float,
    headers: Mapping[str, str],
    download_bytes: int | None = None,
    duration_seconds: float | None = None,
) -> tuple[int, float]:
    if download_bytes is not None and "__down?bytes=" in url:
        import re

        url = re.sub(r"bytes=\d+", f"bytes={download_bytes}", url)

    request = urllib.request.Request(url, headers=dict(headers), method="GET")
    started_at = time.perf_counter()
    bytes_read = 0
    with _open(request, timeout=timeout) as response:
        while True:
            chunk = response.read(1024 * 64)
            if not chunk:
                break
            bytes_read += len(chunk)
            if duration_seconds is not None and (time.perf_counter() - started_at) >= duration_seconds:
                break
    elapsed = time.perf_counter() - started_at
    return bytes_read, elapsed


def _measure_upload(
    url: str,
    *,
    upload_bytes: int,
    timeout: float,
    headers: Mapping[str, str],
) -> tuple[int, float]:
    payload = os.urandom(upload_bytes)
    request = urllib.request.Request(
        url,
        data=payload,
        headers={
            **dict(headers),
            "Content-Type": "application/octet-stream",
        },
        method="POST",
    )
    started_at = time.perf_counter()
    _open(request, timeout=timeout).close()
    elapsed = time.perf_counter() - started_at
    return upload_bytes, elapsed


def _open(request: urllib.request.Request, *, timeout: float):
    try:
        return urllib.request.urlopen(request, timeout=timeout)
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise SpeedtestError(f"speedtest request failed: {exc}") from exc


def _mbps(byte_count: int, seconds: float) -> float:
    if seconds <= 0:
        return 0.0
    return (byte_count * 8) / seconds / 1_000_000
