"""Example running speed tests with full metrics and server validation."""

from pynetscope import DEFAULT_TARGETS, run_speedtest

print("Selecting the best speedtest target and running diagnostics...")
print("WARNING: This will generate traffic to public speedtest endpoints.")

try:
    # Run speedtest with custom download size and duration limits
    result = run_speedtest(
        targets=DEFAULT_TARGETS,
        upload_bytes=500_000,
        download_bytes=5_000_000,
        duration_seconds=3.0,
        latency_samples=5,
        warmup=True
    )
    
    print("\nDiagnostics Result:")
    print(f"Target Server:         {result.target_name}")
    print(f"Latency (Median):      {result.latency_ms:.2f} ms")
    print(f"Latency (Min/Max):     {result.min_latency_ms:.2f} / {result.max_latency_ms:.2f} ms")
    print(f"Jitter:                {result.jitter_ms:.2f} ms")
    print(f"Packet Failure Rate:   {result.packet_failure_rate:.1%}")
    print(f"Download Throughput:   {result.download_mbps:.2f} Mbps")
    print(f"Upload Throughput:     {result.upload_mbps:.2f} Mbps")

except Exception as e:
    print(f"Speedtest failed: {e}")
