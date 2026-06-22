"""sqlite3 database client interceptor."""

from __future__ import annotations

import sqlite3
import time
from typing import Any


class _WrappedCursor:
    def __init__(self, cursor: sqlite3.Cursor, db_name: str, scope: Any) -> None:
        self._cursor = cursor
        self._db_name = db_name
        self._scope = scope

    def execute(self, sql: str, parameters: Any = None) -> Any:
        started = time.perf_counter()
        try:
            if parameters is not None:
                res = self._cursor.execute(sql, parameters)
            else:
                res = self._cursor.execute(sql)
            latency_ms = (time.perf_counter() - started) * 1000
            
            self._scope.record(
                method="QUERY",
                url=f"sqlite://{self._db_name}",
                status_code=200,
                latency_ms=latency_ms,
                metadata={"query": sql},
            )
            return res
        except Exception as exc:
            latency_ms = (time.perf_counter() - started) * 1000
            from .core import normalise_error
            self._scope.record(
                method="QUERY",
                url=f"sqlite://{self._db_name}",
                status_code=None,
                latency_ms=latency_ms,
                error=normalise_error(exc),
                metadata={"query": sql},
            )
            raise

    def executemany(self, sql: str, seq_of_parameters: Any) -> Any:
        started = time.perf_counter()
        try:
            res = self._cursor.executemany(sql, seq_of_parameters)
            latency_ms = (time.perf_counter() - started) * 1000
            self._scope.record(
                method="QUERY",
                url=f"sqlite://{self._db_name}",
                status_code=200,
                latency_ms=latency_ms,
                metadata={"query": sql},
            )
            return res
        except Exception as exc:
            latency_ms = (time.perf_counter() - started) * 1000
            from .core import normalise_error
            self._scope.record(
                method="QUERY",
                url=f"sqlite://{self._db_name}",
                status_code=None,
                latency_ms=latency_ms,
                error=normalise_error(exc),
                metadata={"query": sql},
            )
            raise

    def __getattr__(self, name: str) -> Any:
        return getattr(self._cursor, name)


class _WrappedConnection:
    def __init__(self, conn: sqlite3.Connection, db_name: str, scope: Any) -> None:
        self._conn = conn
        self._db_name = db_name
        self._scope = scope

    def cursor(self, *args: Any, **kwargs: Any) -> _WrappedCursor:
        cursor = self._conn.cursor(*args, **kwargs)
        return _WrappedCursor(cursor, self._db_name, self._scope)

    def execute(self, sql: str, parameters: Any = None) -> Any:
        cursor = self.cursor()
        return cursor.execute(sql, parameters)

    def executemany(self, sql: str, seq_of_parameters: Any) -> Any:
        cursor = self.cursor()
        return cursor.executemany(sql, seq_of_parameters)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._conn, name)

    def __enter__(self) -> _WrappedConnection:
        self._conn.__enter__()
        return self

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> Any:
        return self._conn.__exit__(exc_type, exc_val, exc_tb)


def instrument_sqlite(scope: Any) -> Any:
    """Monkey-patch sqlite3.connect to observe queries."""
    from .instrumentation import install_multi_scope_patch
    
    def wrapper_factory(original: Any, get_scopes: Any) -> Any:
        def wrapped(database: Any, *args: Any, **kwargs: Any) -> Any:
            scopes = get_scopes()
            conn = original(database, *args, **kwargs)
            if scopes:
                return _WrappedConnection(conn, str(database), scopes[0])
            return conn
        return wrapped

    return install_multi_scope_patch(
        owner=sqlite3,
        attribute="connect",
        scope=scope,
        wrapper_factory=wrapper_factory,
    )
