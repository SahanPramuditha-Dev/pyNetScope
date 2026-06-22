"""Core observation engine and optional client interceptors."""

from __future__ import annotations

import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from typing import Any
from urllib.parse import urlsplit

from .alerts import AlertEngine
from .health import HealthEngine
from .instrumentation import InstrumentationHandle, install_multi_scope_patch
from .metrics import MetricsStore
from .models import RequestContext, RequestRecord
from .plugins import PluginRegistry
from .session import inspect_session
from .tracing import current_trace_id, inject_headers
from .utils import EndpointNormalizer, scrub_headers, scrub_payload, scrub_url


class NetScope:
    def __init__(
        self,
        *,
        metrics: MetricsStore | None = None,
        health: HealthEngine | None = None,
        alerts: AlertEngine | None = None,
        plugins: PluginRegistry | None = None,
        inject_trace_headers: bool = True,
        endpoint_normalizer: EndpointNormalizer | None = None,
        normalization_rules: list[tuple[str, str]] | None = None,
        collapse_numeric_ids: bool = True,
        collapse_uuid_ids: bool = True,
        include_query_keys: bool = False,
        payload_capture_chars: int = 2_000,
        flushing_hooks: list[Callable[[RequestRecord], None]] | None = None,
    ) -> None:
        self.metrics = metrics or MetricsStore()
        self.health = health or HealthEngine()
        self.alerts = alerts or AlertEngine(sinks=[])
        self.plugins = plugins or PluginRegistry()
        self.inject_trace_headers = inject_trace_headers

        if endpoint_normalizer is not None:
            self.endpoint_normalizer = endpoint_normalizer
        else:
            from .utils import endpoint_normalizer as build_normalizer

            self.endpoint_normalizer = build_normalizer(
                collapse_numeric_ids=collapse_numeric_ids,
                collapse_uuid_ids=collapse_uuid_ids,
                include_query_keys=include_query_keys,
                rules=normalization_rules,
            )

        self.payload_capture_chars = payload_capture_chars
        self.flushing_hooks = flushing_hooks or []

    @contextmanager
    def observe(
        self,
        method: str,
        url: str,
        *,
        headers: dict[str, str] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> Iterator[RequestContext]:
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

    def record(
        self,
        method: str,
        url: str,
        status_code: int | None,
        latency_ms: float,
        *,
        error: str | None = None,
        request_headers: dict[str, str] | None = None,
        response_headers: dict[str, str] | None = None,
        request_body: Any = None,
        response_body: Any = None,
        metadata: dict[str, Any] | None = None,
    ) -> RequestRecord:
        session_signal = inspect_session(status_code, response_headers or {})
        next_metadata = {**(metadata or {}), "session": session_signal}
        if request_body is not None:
            next_metadata["request_body"] = scrub_payload(request_body, max_chars=self.payload_capture_chars)
        if response_body is not None:
            next_metadata["response_body"] = scrub_payload(response_body, max_chars=self.payload_capture_chars)

        host = urlsplit(url).netloc
        service = (metadata or {}).get("service") or (request_headers or {}).get("x-service-name")

        record = RequestRecord(
            method=method.upper(),
            url=scrub_url(url),
            endpoint=self.endpoint_normalizer(method, url),
            status_code=status_code,
            latency_ms=latency_ms,
            ok=error is None and status_code is not None and status_code < 500,
            error=error,
            trace_id=current_trace_id(),
            request_headers=scrub_headers(request_headers),
            response_headers=scrub_headers(response_headers),
            metadata=next_metadata,
            host=host,
            service=service,
        )
        record = self.plugins.post_request(record)
        self.metrics.add(record)

        for hook in self.flushing_hooks:
            try:
                hook(record)
            except Exception:
                pass

        snapshot = self.metrics.snapshot().get(record.endpoint)
        if snapshot:
            self.alerts.evaluate(snapshot)
        return record

    def instrument_requests(self) -> InstrumentationHandle:
        try:
            import requests.sessions
        except ImportError as exc:
            raise RuntimeError("requests is not installed") from exc

        def wrapper_factory(original, get_scopes):
            def wrapped(session, method, url, **kwargs):
                scopes = get_scopes()
                started = time.perf_counter()
                if any(scope.inject_trace_headers for scope in scopes):
                    kwargs["headers"] = inject_headers(kwargs.get("headers"))
                request_body = kwargs.get("json", kwargs.get("data"))
                try:
                    response = original(session, method, url, **kwargs)
                    latency_ms = (time.perf_counter() - started) * 1000

                    response_body = None
                    try:
                        response_body = response.text
                    except Exception:
                        pass

                    for scope in scopes:
                        scope.record(
                            method,
                            url,
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
                            url,
                            None,
                            latency_ms,
                            error=normalise_error(exc),
                            request_headers=kwargs.get("headers"),
                            request_body=request_body,
                        )
                    raise

            return wrapped

        return install_multi_scope_patch(
            owner=requests.sessions.Session,
            attribute="request",
            scope=self,
            wrapper_factory=wrapper_factory,
        )

    @contextmanager
    def instrumented_requests(self) -> Iterator[InstrumentationHandle]:
        handle = self.instrument_requests()
        try:
            allowed = yield handle
            return allowed
        finally:
            handle.uninstall()

    def instrument_session(self, session: Any) -> None:
        """Instrument a specific requests.Session using our HTTPAdapter."""
        from .session import NetScopeHTTPAdapter

        adapter = NetScopeHTTPAdapter(self)
        session.mount("http://", adapter)
        session.mount("https://", adapter)

    def instrument_httpx(self) -> InstrumentationHandle:
        try:
            import httpx
        except ImportError as exc:
            raise RuntimeError("httpx is not installed") from exc

        def wrapper_factory(original, get_scopes):
            def wrapped(client, method, url, **kwargs):
                scopes = get_scopes()
                started = time.perf_counter()
                if any(scope.inject_trace_headers for scope in scopes):
                    kwargs["headers"] = inject_headers(kwargs.get("headers"))
                request_body = kwargs.get("json", kwargs.get("data"))
                try:
                    response = original(client, method, url, **kwargs)
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
            owner=httpx.Client,
            attribute="request",
            scope=self,
            wrapper_factory=wrapper_factory,
        )

    @contextmanager
    def instrumented_httpx(self) -> Iterator[InstrumentationHandle]:
        handle = self.instrument_httpx()
        try:
            yield handle
        finally:
            handle.uninstall()

    def instrument_httpx_client(self, client: Any) -> None:
        """Instrument a specific httpx.Client using event hooks."""

        def request_hook(request: Any) -> None:
            request.extensions["pynetscope_start"] = time.perf_counter()
            if self.inject_trace_headers:
                from .tracing import inject_headers

                inject_headers(request.headers)

        def response_hook(response: Any) -> None:
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

    def instrument_urllib(self) -> InstrumentationHandle:
        """Instrument urllib.request.urlopen."""
        import urllib.request

        def wrapper_factory(original: Any, get_scopes: Any) -> Any:
            def wrapped(url: Any, data: Any = None, timeout: Any = None, *args: Any, **kwargs: Any) -> Any:
                scopes = get_scopes()
                started = time.perf_counter()

                if isinstance(url, urllib.request.Request):
                    method = url.get_method()
                    full_url = url.full_url
                    req_headers = dict(url.headers)
                    if any(scope.inject_trace_headers for scope in scopes):
                        from .tracing import inject_headers

                        for k, v in inject_headers(req_headers).items():
                            url.add_header(k, v)
                else:
                    method = "POST" if data is not None else "GET"
                    full_url = str(url)
                    req_headers = {}

                try:
                    response = original(url, *args, data=data, timeout=timeout, **kwargs)
                    latency_ms = (time.perf_counter() - started) * 1000
                    resp_headers = dict(getattr(response, "headers", {}))

                    for scope in scopes:
                        scope.record(
                            method,
                            full_url,
                            getattr(response, "status", getattr(response, "code", 200)),
                            latency_ms,
                            request_headers=req_headers,
                            response_headers=resp_headers,
                            request_body=data,
                        )
                    return response
                except Exception as exc:
                    latency_ms = (time.perf_counter() - started) * 1000
                    for scope in scopes:
                        scope.record(
                            method,
                            full_url,
                            None,
                            latency_ms,
                            error=normalise_error(exc),
                            request_headers=req_headers,
                            request_body=data,
                        )
                    raise

            return wrapped

        return install_multi_scope_patch(
            owner=urllib.request,
            attribute="urlopen",
            scope=self,
            wrapper_factory=wrapper_factory,
        )

    def snapshot(self) -> dict[str, Any]:
        metrics = self.metrics.snapshot()
        records = self.metrics.records()
        grouped = {endpoint: [record for record in records if record.endpoint == endpoint] for endpoint in metrics}
        health = {endpoint: self.health.score(item, grouped.get(endpoint, [])) for endpoint, item in metrics.items()}
        recent_alerts = list(self.alerts.recent_alerts) if hasattr(self.alerts, "recent_alerts") else []
        return {"metrics": metrics, "health": health, "records": records, "alerts": recent_alerts}


def normalise_error(exc: Exception) -> str:
    return f"{exc.__class__.__name__}: {exc}"
