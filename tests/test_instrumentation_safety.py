import pytest

from pynetscope import NetScope, endpoint_normalizer


def test_requests_instrumentation_is_idempotent_for_same_scope(http_server):
    requests = pytest.importorskip("requests")
    scope = NetScope()
    original = requests.sessions.Session.request

    first = scope.instrument_requests()
    second = scope.instrument_requests()
    try:
        response = requests.get(f"{http_server}/ok", timeout=5)
    finally:
        first.uninstall()
        second.uninstall()

    assert response.status_code == 200
    assert requests.sessions.Session.request is original
    assert len(scope.snapshot()["records"]) == 1


def test_requests_instrumentation_is_multi_instance_safe(http_server):
    requests = pytest.importorskip("requests")
    first_scope = NetScope()
    second_scope = NetScope()
    original = requests.sessions.Session.request

    first = first_scope.instrument_requests()
    second = second_scope.instrument_requests()
    try:
        requests.get(f"{http_server}/ok", timeout=5)
        first.uninstall()
        assert requests.sessions.Session.request is not original
        requests.get(f"{http_server}/ok", timeout=5)
    finally:
        second.uninstall()

    assert requests.sessions.Session.request is original
    assert len(first_scope.snapshot()["records"]) == 1
    assert len(second_scope.snapshot()["records"]) == 2


def test_payload_scrubbing_and_custom_endpoint_normalizer():
    normalizer = endpoint_normalizer(include_query_keys=True)
    scope = NetScope(endpoint_normalizer=normalizer)

    record = scope.record(
        "POST",
        "https://api.example.com/users/123?sort=asc&token=secret",
        200,
        12.0,
        request_body={"token": "secret", "nested": {"password": "hidden", "name": "Ada"}},
    )

    assert record.endpoint == "POST api.example.com/users/:id?sort&token"
    assert record.metadata["request_body"]["token"] == "[redacted]"
    assert record.metadata["request_body"]["nested"]["password"] == "[redacted]"
    assert record.metadata["request_body"]["nested"]["name"] == "Ada"
