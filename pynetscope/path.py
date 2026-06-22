"""Network path inspection helpers."""

from __future__ import annotations

import socket
import ssl
import time
from dataclasses import dataclass
from urllib.parse import urlsplit


@dataclass(frozen=True)
class PathInspection:
    host: str
    port: int
    dns_ms: float
    tcp_ms: float
    tls_ms: float | None


@dataclass(frozen=True)
class HTTPTiming:
    host: str
    port: int
    dns_ms: float
    tcp_ms: float
    tls_ms: float | None
    ttfb_ms: float
    total_ms: float
    status_line: str


def inspect_path(url: str, timeout: float = 5.0) -> PathInspection:
    parts = urlsplit(url)
    host = parts.hostname
    if not host:
        raise ValueError("url must include a host")
    port = parts.port or (443 if parts.scheme == "https" else 80)

    started = time.perf_counter()
    addresses = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    dns_ms = (time.perf_counter() - started) * 1000

    family, socktype, proto, _, sockaddr = addresses[0]
    sock = socket.socket(family, socktype, proto)
    sock.settimeout(timeout)
    started = time.perf_counter()
    sock.connect(sockaddr)
    tcp_ms = (time.perf_counter() - started) * 1000

    tls_ms = None
    try:
        if parts.scheme == "https":
            context = ssl.create_default_context()
            started = time.perf_counter()
            tls_sock = context.wrap_socket(sock, server_hostname=host)
            tls_ms = (time.perf_counter() - started) * 1000
            tls_sock.close()
        else:
            sock.close()
    except Exception:
        sock.close()
        raise

    return PathInspection(host=host, port=port, dns_ms=dns_ms, tcp_ms=tcp_ms, tls_ms=tls_ms)


def inspect_http_timing(url: str, timeout: float = 5.0) -> HTTPTiming:
    parts = urlsplit(url)
    host = parts.hostname
    if not host:
        raise ValueError("url must include a host")
    port = parts.port or (443 if parts.scheme == "https" else 80)
    path = parts.path or "/"
    if parts.query:
        path = f"{path}?{parts.query}"

    total_started = time.perf_counter()
    dns_started = time.perf_counter()
    addresses = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    dns_ms = (time.perf_counter() - dns_started) * 1000

    family, socktype, proto, _, sockaddr = addresses[0]
    raw_sock = socket.socket(family, socktype, proto)
    raw_sock.settimeout(timeout)

    tcp_started = time.perf_counter()
    raw_sock.connect(sockaddr)
    tcp_ms = (time.perf_counter() - tcp_started) * 1000

    tls_ms = None
    sock = raw_sock
    if parts.scheme == "https":
        context = ssl.create_default_context()
        tls_started = time.perf_counter()
        sock = context.wrap_socket(raw_sock, server_hostname=host)
        tls_ms = (time.perf_counter() - tls_started) * 1000

    request = (
        f"GET {path} HTTP/1.1\r\nHost: {host}\r\nUser-Agent: pyNetScope/0.1 path\r\nConnection: close\r\n\r\n"
    ).encode("ascii")

    try:
        sock.sendall(request)
        first_byte_started = time.perf_counter()
        first = sock.recv(1)
        ttfb_ms = (time.perf_counter() - first_byte_started) * 1000
        response = bytearray(first)
        while b"\r\n" not in response:
            chunk = sock.recv(1)
            if not chunk:
                break
            response.extend(chunk)
        status_line = response.decode("iso-8859-1", errors="replace").splitlines()[0] if response else ""
        while sock.recv(1024 * 16):
            pass
    finally:
        sock.close()

    total_ms = (time.perf_counter() - total_started) * 1000
    return HTTPTiming(
        host=host,
        port=port,
        dns_ms=dns_ms,
        tcp_ms=tcp_ms,
        tls_ms=tls_ms,
        ttfb_ms=ttfb_ms,
        total_ms=total_ms,
        status_line=status_line,
    )
