"""FastAPI and ASGI middleware integration."""

from __future__ import annotations

import time
from collections.abc import Awaitable, Callable
from typing import Any

from .core import NetScope, normalise_error

ASGIReceive = Callable[[], Awaitable[dict[str, Any]]]
ASGISend = Callable[[dict[str, Any]], Awaitable[None]]
ASGIApp = Callable[[dict[str, Any], ASGIReceive, ASGISend], Awaitable[None]]


class PyNetScopeMiddleware:
    """ASGI middleware for inbound request observations."""

    def __init__(self, app: ASGIApp, scope: NetScope | None = None) -> None:
        self.app = app
        self.scope = scope or NetScope()

    async def __call__(self, asgi_scope: dict[str, Any], receive: ASGIReceive, send: ASGISend) -> None:
        if asgi_scope.get("type") != "http":
            await self.app(asgi_scope, receive, send)
            return

        started = time.perf_counter()
        status_code: int | None = None
        response_headers: dict[str, str] = {}

        async def send_wrapper(message: dict[str, Any]) -> None:
            nonlocal status_code, response_headers
            if message.get("type") == "http.response.start":
                status_code = int(message.get("status", 0))
                response_headers = {
                    key.decode("latin-1"): value.decode("latin-1") for key, value in message.get("headers", [])
                }
            await send(message)

        method = str(asgi_scope.get("method", "GET"))
        url = _asgi_url(asgi_scope)
        request_headers = {
            key.decode("latin-1"): value.decode("latin-1") for key, value in asgi_scope.get("headers", [])
        }

        try:
            await self.app(asgi_scope, receive, send_wrapper)
        except Exception as exc:
            latency_ms = (time.perf_counter() - started) * 1000
            self.scope.record(
                method,
                url,
                status_code,
                latency_ms,
                error=normalise_error(exc),
                request_headers=request_headers,
                response_headers=response_headers,
            )
            raise

        latency_ms = (time.perf_counter() - started) * 1000
        self.scope.record(
            method,
            url,
            status_code,
            latency_ms,
            request_headers=request_headers,
            response_headers=response_headers,
        )


def instrument_fastapi_app(app: Any, scope: NetScope | None = None) -> NetScope:
    """Attach pyNetScope middleware to a FastAPI/Starlette app."""

    selected_scope = scope or NetScope()
    app.add_middleware(PyNetScopeMiddleware, scope=selected_scope)
    return selected_scope


def _asgi_url(asgi_scope: dict[str, Any]) -> str:
    scheme = asgi_scope.get("scheme", "http")
    headers = {key.decode("latin-1").lower(): value.decode("latin-1") for key, value in asgi_scope.get("headers", [])}
    host = headers.get("host")
    if not host:
        server = asgi_scope.get("server") or ("localhost", 80)
        host = f"{server[0]}:{server[1]}"
    path = asgi_scope.get("root_path", "") + asgi_scope.get("path", "/")
    query = asgi_scope.get("query_string", b"").decode("latin-1")
    return f"{scheme}://{host}{path}" + (f"?{query}" if query else "")
