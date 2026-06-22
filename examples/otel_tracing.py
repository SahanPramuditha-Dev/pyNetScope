"""Example showing retrospective span creation and OTel/OTLP exporter setup."""

from pynetscope import NetScope
from pynetscope.otel import create_span, setup_otlp_exporter

# 1. Optionally configure the OTLP exporter to send traces to a local collector
print("Setting up OTLP exporter...")
otlp_configured = setup_otlp_exporter(endpoint="http://localhost:4317")
print(f"OTLP Exporter Configured: {otlp_configured} (requires opentelemetry-sdk)")

# 2. Record request metrics
scope = NetScope()
print("\nRecording a manual request observation...")
record = scope.record(
    method="GET",
    url="https://api.example.com/v1/orders",
    status_code=200,
    latency_ms=125.4,
    request_body='{"user_id": 42}'
)

# 3. Create a retrospective trace span in the OpenTelemetry system matching the request timing
span_created = create_span(record)
print(f"Retrospective span created: {span_created}")
if span_created:
    print("A span was generated matching the start and end times of the actual HTTP request.")
else:
    print("No span created (OpenTelemetry is either not installed or not initialized).")
