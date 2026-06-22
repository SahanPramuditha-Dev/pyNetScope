"""Snapshot exporters."""

from __future__ import annotations

import csv
import json
import sqlite3
from collections.abc import Mapping
from pathlib import Path

from .models import EndpointMetrics, HealthStatus, RequestRecord
from .utils import to_plain


def export_json(
    metrics: Mapping[str, EndpointMetrics],
    health: Mapping[str, HealthStatus] | None = None,
) -> str:
    return json.dumps({"metrics": to_plain(dict(metrics)), "health": to_plain(dict(health or {}))}, indent=2)


def export_prometheus(metrics: Mapping[str, EndpointMetrics]) -> str:
    lines = [
        "# HELP pynetscope_requests_total Total observed HTTP requests.",
        "# TYPE pynetscope_requests_total counter",
    ]
    for endpoint, item in metrics.items():
        label = _label(endpoint)
        parts = endpoint.split(" ", 1)
        method = parts[0] if len(parts) == 2 else "GET"
        lines.append(f'pynetscope_requests_total{{endpoint="{label}",method="{method}"}} {item.count}')
        lines.append(f'pynetscope_request_failures_total{{endpoint="{label}",method="{method}"}} {item.failure_count}')
        lines.append(f'pynetscope_request_latency_p95_ms{{endpoint="{label}",method="{method}"}} {item.p95_ms:.6f}')
        lines.append(
            f'pynetscope_request_success_ratio{{endpoint="{label}",method="{method}"}} {item.success_ratio:.6f}'
        )
    return "\n".join(lines) + "\n"


def export_csv(records: list[RequestRecord], path: str | Path, append: bool = False) -> None:
    rows = [to_plain(record) for record in records]
    fieldnames = list(rows[0].keys()) if rows else list(RequestRecord.__dataclass_fields__.keys())
    mode = "a" if append else "w"
    file_exists = Path(path).exists() and Path(path).stat().st_size > 0
    with Path(path).open(mode, newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        if not (append and file_exists):
            writer.writeheader()
        writer.writerows(rows)


def export_ndjson(records: list[RequestRecord], path: str | Path, append: bool = False) -> None:
    mode = "a" if append else "w"
    with Path(path).open(mode, encoding="utf-8") as handle:
        for record in records:
            handle.write(json.dumps(to_plain(record)) + "\n")


class SQLiteExporter:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self._migrate()

    def _migrate(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(self.path) as conn:
            conn.execute("CREATE TABLE IF NOT EXISTS schema_version (version INTEGER)")
            cursor = conn.execute("SELECT version FROM schema_version")
            row = cursor.fetchone()
            current_version = row[0] if row else 0

            if current_version < 1:
                conn.execute(
                    """
                    CREATE TABLE IF NOT EXISTS requests (
                        timestamp_ms REAL,
                        method TEXT,
                        url TEXT,
                        endpoint TEXT,
                        status_code INTEGER,
                        latency_ms REAL,
                        ok INTEGER,
                        error TEXT,
                        trace_id TEXT
                    )
                    """
                )
                if not row:
                    conn.execute("INSERT INTO schema_version VALUES (1)")
                else:
                    conn.execute("UPDATE schema_version SET version = 1")
                current_version = 1

            if current_version < 2:
                for col in ["host TEXT", "service TEXT", "request_body TEXT", "response_body TEXT"]:
                    try:
                        conn.execute(f"ALTER TABLE requests ADD COLUMN {col}")
                    except sqlite3.OperationalError:
                        pass
                conn.execute("UPDATE schema_version SET version = 2")

    def write_records(self, records: list[RequestRecord]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(self.path) as conn:
            conn.executemany(
                """
                INSERT INTO requests VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        record.timestamp_ms,
                        record.method,
                        record.url,
                        record.endpoint,
                        record.status_code,
                        record.latency_ms,
                        int(record.ok),
                        record.error,
                        record.trace_id,
                        record.host,
                        record.service,
                        json.dumps(record.metadata.get("request_body"))
                        if record.metadata.get("request_body")
                        else None,
                        json.dumps(record.metadata.get("response_body"))
                        if record.metadata.get("response_body")
                        else None,
                    )
                    for record in records
                ],
            )

    def read_records(self, limit: int = 1000) -> list[RequestRecord]:
        if not self.path.exists():
            return []
        with sqlite3.connect(self.path) as conn:
            conn.row_factory = sqlite3.Row
            try:
                cursor = conn.execute(
                    "SELECT * FROM requests ORDER BY timestamp_ms DESC LIMIT ?",
                    (limit,),
                )
                rows = cursor.fetchall()
            except sqlite3.OperationalError:
                return []
            records = []
            for row in rows:
                metadata = {}
                keys = row.keys()
                
                req_body = row["request_body"] if "request_body" in keys else None
                resp_body = row["response_body"] if "response_body" in keys else None
                
                if req_body:
                    try:
                        metadata["request_body"] = json.loads(req_body)
                    except Exception:
                        metadata["request_body"] = req_body
                if resp_body:
                    try:
                        metadata["response_body"] = json.loads(resp_body)
                    except Exception:
                        metadata["response_body"] = resp_body

                record = RequestRecord(
                    timestamp_ms=row["timestamp_ms"],
                    method=row["method"],
                    url=row["url"],
                    endpoint=row["endpoint"],
                    status_code=row["status_code"],
                    latency_ms=row["latency_ms"],
                    ok=bool(row["ok"]),
                    error=row["error"],
                    trace_id=row["trace_id"],
                    host=row["host"] if "host" in keys else None,
                    service=row["service"] if "service" in keys else None,
                    metadata=metadata,
                )
                records.append(record)
            records.reverse()
            return records

    def prune_records(self, before_timestamp_ms: float) -> None:
        with sqlite3.connect(self.path) as conn:
            conn.execute("DELETE FROM requests WHERE timestamp_ms < ?", (before_timestamp_ms,))


def _label(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")
