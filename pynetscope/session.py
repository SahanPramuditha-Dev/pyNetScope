"""Session and auth signal extraction."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from http.cookies import SimpleCookie
from typing import Any


@dataclass(frozen=True)
class SessionSignal:
    auth_failed: bool = False
    token_expired: bool = False
    session_cookie: str | None = None


def inspect_session(status_code: int | None, headers: Mapping[str, str]) -> SessionSignal:
    lowered = {key.lower(): value for key, value in headers.items()}
    auth_failed = status_code in {401, 403}
    www_auth = lowered.get("www-authenticate", "").lower()
    token_expired = "expired" in www_auth or "invalid_token" in www_auth
    session_cookie = _session_cookie(lowered.get("set-cookie", ""))
    return SessionSignal(auth_failed=auth_failed, token_expired=token_expired, session_cookie=session_cookie)


def _session_cookie(header: str) -> str | None:
    if not header:
        return None
    cookie = SimpleCookie()
    cookie.load(header)
    for name in cookie:
        if "session" in name.lower() or name.lower() in {"sid", "jsessionid"}:
            return name
    return None


try:
    from requests.adapters import HTTPAdapter
except ImportError:

    class HTTPAdapter:  # type: ignore
        pass


class NetScopeHTTPAdapter(HTTPAdapter):
    """requests.Session-scoped adapter alternative to global monkey-patching."""

    def __init__(self, scope: Any, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.scope = scope

    def send(
        self,
        request: Any,
        stream: bool = False,
        timeout: Any = None,
        verify: Any = True,
        cert: Any = None,
        proxies: Any = None,
    ) -> Any:
        import time

        started = time.perf_counter()
        request_headers = dict(request.headers)
        request_body = request.body

        if self.scope.inject_trace_headers:
            from .tracing import inject_headers

            for k, v in inject_headers(request_headers).items():
                request.headers[k] = v
                request_headers[k] = v

        try:
            response = super().send(request, stream=stream, timeout=timeout, verify=verify, cert=cert, proxies=proxies)
            latency_ms = (time.perf_counter() - started) * 1000

            response_body = None
            if not stream:
                try:
                    response_body = response.text
                except Exception:
                    pass

            self.scope.record(
                request.method,
                request.url,
                response.status_code,
                latency_ms,
                request_headers=request_headers,
                response_headers=dict(response.headers),
                request_body=request_body,
                response_body=response_body,
            )
            return response
        except Exception as exc:
            latency_ms = (time.perf_counter() - started) * 1000
            from .core import normalise_error

            self.scope.record(
                request.method,
                request.url,
                None,
                latency_ms,
                error=normalise_error(exc),
                request_headers=request_headers,
                request_body=request_body,
            )
            raise
