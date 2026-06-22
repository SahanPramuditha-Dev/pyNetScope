"""Async helpers for pyNetScope."""

from __future__ import annotations

import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from .core import NetScope, normalise_error
from .instrumentation import InstrumentationHandle, install_multi_scope_patch
from .models import RequestContext
from .tracing import current_trace_id, inject_headers
from .utils import scrub_headers, scrub_url


class AsyncNetScope(NetScope):
    @asynccontextmanager
    async def observe_async(
        self,
        method: str,
        url: str,
        *,
        headers: dict[str, str] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> AsyncIterator[RequestContext]:
        started = time.perf_counter()
        trace_headers = inject_headers(headers) if self.inject_trace_headers else dict(headers or {})
        context = RequestContext(
            method=method.upper(),
            url=scrub_url(url),
            headers=scrub_headers(trace_headers),
            trace_id=current_trace_id(),
            metadata=metadata or {},
        )
        context = self.plugins.pre_request(context)
        try:
            yield context
        except Exception as exc:
            latency_ms = (time.perf_counter() - started) * 1000
            self.record(method, url, None, latency_ms, error=normalise_error(exc), request_headers=trace_headers)
            raise

    def instrument_httpx_async(self) -> InstrumentationHandle:
        try:
            import httpx
        except ImportError as exc:
            raise RuntimeError("httpx is not installed") from exc

        def wrapper_factory(original, get_scopes):
            async def wrapped(client, method, url, **kwargs):
                scopes = get_scopes()
                started = time.perf_counter()
                if any(scope.inject_trace_headers for scope in scopes):
                    kwargs["headers"] = inject_headers(kwargs.get("headers"))
                request_body = kwargs.get("json", kwargs.get("data"))
                try:
                    response = await original(client, method, url, **kwargs)
                    latency_ms = (time.perf_counter() - started) * 1000

                    response_body = None
                    try:
                        response_body = response.text
                    except Exception:
                        pass

                    for scope in scopes:
                        scope.record(
                            method,
                            str(url),
                            response.status_code,
                            latency_ms,
                            request_headers=kwargs.get("headers"),
                            response_headers=dict(response.headers),
                            request_body=request_body,
                            response_body=response_body,
                        )
                    return response
                except Exception as exc:
                    latency_ms = (time.perf_counter() - started) * 1000
                    for scope in scopes:
                        scope.record(
                            method,
                            str(url),
                            None,
                            latency_ms,
                            error=normalise_error(exc),
                            request_headers=kwargs.get("headers"),
                            request_body=request_body,
                        )
                    raise

            return wrapped

        return install_multi_scope_patch(
            owner=httpx.AsyncClient,
            attribute="request",
            scope=self,
            wrapper_factory=wrapper_factory,
        )

    @asynccontextmanager
    async def instrumented_httpx_async(self) -> AsyncIterator[InstrumentationHandle]:
        handle = self.instrument_httpx_async()
        try:
            yield handle
        finally:
            handle.uninstall()

    def instrument_httpx_client_async(self, client: Any) -> None:
        """Instrument a specific httpx.AsyncClient using async event hooks."""

        async def request_hook(request: Any) -> None:
            request.extensions["pynetscope_start"] = time.perf_counter()
            if self.inject_trace_headers:
                from .tracing import inject_headers

                inject_headers(request.headers)

        async def response_hook(response: Any) -> None:
            request = response.request
            started = request.extensions.get("pynetscope_start")
            latency_ms = (time.perf_counter() - started) * 1000 if started else 0.0

            response_body = None
            try:
                response_body = response.text
            except Exception:
                pass

            self.record(
                request.method,
                str(request.url),
                response.status_code,
                latency_ms,
                request_headers=dict(request.headers),
                response_headers=dict(response.headers),
                request_body=getattr(request, "_content", None),
                response_body=response_body,
            )

        client.event_hooks["request"].append(request_hook)
        client.event_hooks["response"].append(response_hook)

    def instrument_aiohttp(self) -> InstrumentationHandle:
        try:
            import aiohttp
        except ImportError as exc:
            raise RuntimeError("aiohttp is not installed") from exc

        def wrapper_factory(original, get_scopes):
            async def wrapped(session, method, url, **kwargs):
                scopes = get_scopes()
                started = time.perf_counter()
                if any(scope.inject_trace_headers for scope in scopes):
                    kwargs["headers"] = inject_headers(kwargs.get("headers"))
                request_body = kwargs.get("json", kwargs.get("data"))
                try:
                    response = await original(session, method, url, **kwargs)
                    latency_ms = (time.perf_counter() - started) * 1000

                    response_body = None
                    try:
                        if response.headers.get("Content-Type", "").startswith(("application/json", "text/")):
                            response_body = await response.text()
                    except Exception:
                        pass

                    for scope in scopes:
                        scope.record(
                            method,
                            str(url),
                            response.status,
                            latency_ms,
                            request_headers=kwargs.get("headers"),
                            response_headers=dict(response.headers),
                            request_body=request_body,
                            response_body=response_body,
                        )
                    return response
                except Exception as exc:
                    latency_ms = (time.perf_counter() - started) * 1000
                    for scope in scopes:
                        scope.record(
                            method,
                            str(url),
                            None,
                            latency_ms,
                            error=normalise_error(exc),
                            request_headers=kwargs.get("headers"),
                            request_body=request_body,
                        )
                    raise

            return wrapped

        return install_multi_scope_patch(
            owner=aiohttp.ClientSession,
            attribute="_request",
            scope=self,
            wrapper_factory=wrapper_factory,
        )

    @asynccontextmanager
    async def instrumented_aiohttp(self) -> AsyncIterator[InstrumentationHandle]:
        handle = self.instrument_aiohttp()
        try:
            yield handle
        finally:
            handle.uninstall()
