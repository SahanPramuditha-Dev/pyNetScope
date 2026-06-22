"""Patch lifecycle helpers for optional client instrumentation."""

from __future__ import annotations

import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from types import TracebackType
from typing import Any

WrapperFactory = Callable[[Any, Callable[[], tuple[Any, ...]]], Any]


@dataclass
class _PatchState:
    owner: Any
    attribute: str
    original: Any
    replacement: Any
    scopes: list[Any] = field(default_factory=list)


_LOCK = threading.RLock()
_PATCHES: dict[tuple[int, str], _PatchState] = {}
_GLOBAL_HANDLES: list[InstrumentationHandle] = []


@dataclass
class InstrumentationHandle:
    """A reversible multi-scope monkey patch handle."""

    owner: Any
    attribute: str
    scope: Any
    installed: bool = True

    def uninstall(self) -> None:
        if not self.installed:
            return
        with _LOCK:
            key = (id(self.owner), self.attribute)
            state = _PATCHES.get(key)
            if state and self.scope in state.scopes:
                state.scopes.remove(self.scope)
                if not state.scopes:
                    # Uninstall safety: only restore if the current value matches our replacement
                    current = getattr(state.owner, state.attribute, None)
                    if current is state.replacement:
                        setattr(state.owner, state.attribute, state.original)
                    del _PATCHES[key]
        self.installed = False

    def __enter__(self) -> InstrumentationHandle:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.uninstall()


def install_multi_scope_patch(
    *,
    owner: Any,
    attribute: str,
    scope: Any,
    wrapper_factory: WrapperFactory,
) -> InstrumentationHandle:
    """Install one shared wrapper and register scope as an observer."""

    key = (id(owner), attribute)
    with _LOCK:
        state = _PATCHES.get(key)
        if state is None:
            original = getattr(owner, attribute)
            # Idempotency guard: if original is already patched, find the existing state
            if getattr(original, "_pynetscope_patched", False):
                for s in _PATCHES.values():
                    if s.replacement is original:
                        state = s
                        break

            if state is None:

                def get_scopes() -> tuple[Any, ...]:
                    with _LOCK:
                        current = _PATCHES.get(key)
                        return tuple(current.scopes) if current else ()

                replacement = wrapper_factory(original, get_scopes)
                try:
                    replacement._pynetscope_patched = True
                except (AttributeError, TypeError):
                    pass
                state = _PatchState(owner=owner, attribute=attribute, original=original, replacement=replacement)
                _PATCHES[key] = state
                setattr(owner, attribute, replacement)

        if scope not in state.scopes:
            state.scopes.append(scope)
    return InstrumentationHandle(owner=owner, attribute=attribute, scope=scope)


def install(
    *,
    scope: Any = None,
    requests: bool = True,
    httpx: bool = True,
    aiohttp: bool = True,
    urllib: bool = True,
) -> list[InstrumentationHandle]:
    """Auto-instrument all supported libraries with a single call."""
    global _GLOBAL_HANDLES
    if scope is None:
        # Avoid circular imports by importing core elements inside function
        from .core import NetScope

        try:
            from .async_support import AsyncNetScope

            scope = AsyncNetScope()
        except ImportError:
            scope = NetScope()

    handles = []
    if requests:
        try:
            handles.append(scope.instrument_requests())
        except Exception:
            pass
    if httpx:
        try:
            handles.append(scope.instrument_httpx())
        except Exception:
            pass
        try:
            if hasattr(scope, "instrument_httpx_async"):
                handles.append(scope.instrument_httpx_async())
        except Exception:
            pass
    if aiohttp:
        try:
            if hasattr(scope, "instrument_aiohttp"):
                handles.append(scope.instrument_aiohttp())
        except Exception:
            pass
    if urllib:
        try:
            if hasattr(scope, "instrument_urllib"):
                handles.append(scope.instrument_urllib())
        except Exception:
            pass

    _GLOBAL_HANDLES.extend(handles)
    return handles


def uninstall() -> None:
    """Uninstall all auto-instrumented libraries."""
    global _GLOBAL_HANDLES
    for handle in _GLOBAL_HANDLES:
        handle.uninstall()
    _GLOBAL_HANDLES.clear()
