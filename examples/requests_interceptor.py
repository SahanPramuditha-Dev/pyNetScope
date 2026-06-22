"""Example showing requests.Session-scoped instrumentation using NetScopeHTTPAdapter."""

import requests

from pynetscope import NetScope

# 1. Initialize NetScope
scope = NetScope()

# 2. Setup a standard requests Session
session = requests.Session()

# 3. Mount the NetScope adapter to observe this session's requests
scope.instrument_session(session)

print("Making requests via observed session...")
try:
    # This request will be observed and metrics/latency recorded
    response = session.get("https://httpbin.org/get", timeout=5)
    print(f"Request succeeded with status code: {response.status_code}")
except Exception as e:
    print(f"Request failed: {e}")

# 4. Check the gathered snapshot
snapshot = scope.snapshot()
print("\nMetrics Snapshot:")
for endpoint, metrics in snapshot["metrics"].items():
    print(f"Endpoint: {endpoint}")
    print(f"  Request count: {metrics.count}")
    print(f"  Avg Latency:   {metrics.avg_ms:.2f} ms")
    print(f"  Success Ratio: {metrics.success_ratio:.0%}")
