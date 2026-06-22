"""Django middleware for inbound HTTP request observations."""

from __future__ import annotations

import time
from typing import Any


class PyNetScopeDjangoMiddleware:
    """Django middleware for inbound request observations."""

    def __init__(self, get_response: Any) -> None:
        self.get_response = get_response
        from .core import NetScope

        self.scope = NetScope()

    def __call__(self, request: Any) -> Any:
        started = time.perf_counter()
        response = self.get_response(request)
        latency_ms = (time.perf_counter() - started) * 1000

        request_body = None
        try:
            request_body = request.body
        except Exception:
            pass

        response_body = None
        try:
            response_body = response.content.decode("utf-8")
        except Exception:
            pass

        self.scope.record(
            request.method,
            request.build_absolute_uri(),
            response.status_code,
            latency_ms,
            request_headers={k: v for k, v in request.META.items() if k.startswith("HTTP_")},
            response_headers=dict(response.items()),
            request_body=request_body,
            response_body=response_body,
        )
        return response
