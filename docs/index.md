# pyNetScope Documentation

pyNetScope is an embeddable network observability SDK for Python applications.

Start with:

```python
from pynetscope import NetScope

scope = NetScope()
scope.record("GET", "https://api.example.com", 200, 42)
print(scope.snapshot())
```

## Guides

- API stability: `api-stability.md`
- CLI usage: see the README command examples
- Prometheus: use `PrometheusMetricsServer(scope)`

