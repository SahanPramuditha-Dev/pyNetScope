"""Flask middleware for inbound HTTP request observations."""

from __future__ import annotations

import time
from typing import Any

from .core import NetScope


class PyNetScopeFlaskMiddleware:
    """Flask middleware for inbound request observations."""

    def __init__(self, app: Any, scope: NetScope | None = None) -> None:
        self.app = app
        self.scope = scope or NetScope()

        @app.before_request
        def before_request() -> None:
            import flask

            flask.g.pynetscope_start = time.perf_counter()

        @app.after_request
        def after_request(response: Any) -> Any:
            import flask

            started = getattr(flask.g, "pynetscope_start", None)
            latency_ms = (time.perf_counter() - started) * 1000 if started else 0.0
            req = flask.request

            # Read request body safely if it has text content-type
            request_body = None
            if req.content_type and ("json" in req.content_type or "form" in req.content_type):
                try:
                    request_body = req.get_data(as_text=True)
                except Exception:
                    pass

            self.scope.record(
                req.method,
                req.url,
                response.status_code,
                latency_ms,
                request_headers=dict(req.headers),
                response_headers=dict(response.headers),
                request_body=request_body,
                response_body=response.get_data(as_text=True)
                if not getattr(response, "direct_passthrough", False)
                else None,
            )
            return response
