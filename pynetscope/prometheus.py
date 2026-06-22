"""Prometheus HTTP server integration."""

from __future__ import annotations

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread
from typing import TYPE_CHECKING

from .exporters import export_prometheus

if TYPE_CHECKING:
    from .core import NetScope


class PrometheusMetricsServer:
    """Small embeddable HTTP server exposing `/metrics`."""

    def __init__(self, scope: NetScope, host: str = "127.0.0.1", port: int = 8000) -> None:
        self.scope = scope
        self.host = host
        self.port = port
        self._server = self._build_server()
        self._thread: Thread | None = None

    @property
    def address(self) -> tuple[str, int]:
        host, port = self._server.server_address[:2]
        return str(host), int(port)

    def serve_forever(self) -> None:
        self._server.serve_forever()

    def start_background(self) -> PrometheusMetricsServer:
        self._thread = Thread(target=self.serve_forever, name="pynetscope-prometheus", daemon=True)
        self._thread.start()
        return self

    def shutdown(self) -> None:
        self._server.shutdown()
        self._server.server_close()
        if self._thread:
            self._thread.join(timeout=2)

    def _build_server(self) -> ThreadingHTTPServer:
        scope = self.scope

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:
                if self.path.split("?", 1)[0] != "/metrics":
                    self.send_error(404)
                    return
                payload = export_prometheus(scope.snapshot()["metrics"]).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "text/plain; version=0.0.4; charset=utf-8")
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

            def log_message(self, format: str, *args) -> None:
                return

        return ThreadingHTTPServer((self.host, self.port), Handler)
