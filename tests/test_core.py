from pynetscope import NetScope


def test_record_scrubs_sensitive_data_and_tracks_metrics():
    scope = NetScope()

    record = scope.record(
        "GET",
        "https://api.example.com/users/123?token=secret",
        200,
        42.0,
        request_headers={"Authorization": "Bearer secret", "Accept": "application/json"},
    )

    assert record.url == "https://api.example.com/users/123?token=%5Bredacted%5D"
    assert record.endpoint == "GET api.example.com/users/:id"
    assert record.request_headers["Authorization"] == "[redacted]"

    snapshot = scope.snapshot()
    metrics = snapshot["metrics"][record.endpoint]
    health = snapshot["health"][record.endpoint]

    assert metrics.count == 1
    assert metrics.success_ratio == 1.0
    assert health.state == "Stable"


def test_record_detects_auth_signals():
    scope = NetScope()

    record = scope.record(
        "GET",
        "https://api.example.com/me",
        401,
        10.0,
        response_headers={"WWW-Authenticate": "Bearer error=invalid_token, error_description=expired"},
    )

    assert record.metadata["session"].auth_failed is True
    assert record.metadata["session"].token_expired is True
