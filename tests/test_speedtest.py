from pynetscope.speedtest import SpeedtestResult, SpeedtestTarget, run_speedtest


def test_run_speedtest_uses_probe_results(monkeypatch):
    monkeypatch.setattr("pynetscope.speedtest._measure_latency", lambda *args, **kwargs: 12.5)
    monkeypatch.setattr("pynetscope.speedtest._measure_download", lambda *args, **kwargs: (2_000_000, 1.0))
    monkeypatch.setattr("pynetscope.speedtest._measure_upload", lambda *args, **kwargs: (1_000_000, 2.0))

    result = run_speedtest(target=SpeedtestTarget(name="local"))

    assert result == SpeedtestResult(
        target_name="local",
        latency_ms=12.5,
        download_mbps=16.0,
        upload_mbps=4.0,
        downloaded_bytes=4_000_000,
        uploaded_bytes=2_000_000,
        download_seconds=2.0,
        upload_seconds=4.0,
        download_samples=(16.0, 16.0),
        upload_samples=(4.0, 4.0),
        min_latency_ms=12.5,
        max_latency_ms=12.5,
        jitter_ms=0.0,
        packet_failure_rate=0.0,
    )


def test_run_speedtest_rejects_invalid_upload_size():
    try:
        run_speedtest(upload_bytes=0)
    except ValueError as exc:
        assert "upload_bytes" in str(exc)
    else:
        raise AssertionError("expected ValueError")
