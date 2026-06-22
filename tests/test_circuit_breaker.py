from pynetscope import CircuitBreaker


def test_circuit_breaker_opens_and_blocks_requests():
    breaker = CircuitBreaker(failure_threshold=2, recovery_timeout=60)

    breaker.record_failure()
    assert breaker.allow_request() is True

    breaker.record_failure()
    assert breaker.state == "open"
    assert breaker.allow_request() is False


def test_circuit_breaker_half_open_recovers():
    breaker = CircuitBreaker(failure_threshold=1, recovery_timeout=0)

    breaker.record_failure()
    assert breaker.allow_request() is True
    assert breaker.state == "half-open"

    breaker.record_success()
    assert breaker.state == "closed"
