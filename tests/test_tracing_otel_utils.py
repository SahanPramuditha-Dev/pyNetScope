from pynetscope import NetScope, ensure_trace_id, inject_headers, span_attributes, traceparent
from pynetscope.utils import fingerprint, normalize_endpoint


def test_trace_headers_are_injected():
    trace_id = ensure_trace_id()
    headers = inject_headers({"Accept": "application/json"})

    assert headers["x-request-id"] == trace_id
    assert headers["traceparent"].startswith(f"00-{trace_id}-")


def test_traceparent_accepts_explicit_values():
    value = traceparent("0" * 32, "1" * 16)

    assert value == f"00-{'0' * 32}-{'1' * 16}-01"


def test_otel_span_attributes():
    record = NetScope().record("GET", "https://api.example.com/users/99", 200, 15.0)
    attrs = span_attributes(record)

    assert attrs["http.request.method"] == "GET"
    assert attrs["pynetscope.endpoint"] == "GET api.example.com/users/:id"
    assert attrs["pynetscope.ok"] is True


def test_fingerprint_is_stable_after_endpoint_normalisation():
    first = fingerprint("GET", "https://api.example.com/users/123?token=a")
    second = fingerprint("GET", "https://api.example.com/users/456?token=b")

    assert normalize_endpoint("GET", "https://api.example.com/users/123") == "GET api.example.com/users/:id"
    assert first == second
