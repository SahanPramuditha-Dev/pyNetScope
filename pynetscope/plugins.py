"""Hook-based plugin registry."""

from __future__ import annotations

from importlib import metadata
from typing import Protocol

from .models import RequestContext, RequestRecord


class Plugin(Protocol):
    name: str

    def pre_request(self, context: RequestContext) -> RequestContext: ...

    def post_request(self, record: RequestRecord) -> RequestRecord: ...


class PluginRegistry:
    def __init__(self) -> None:
        self._plugins: list[Plugin] = []

    def register(self, plugin: Plugin) -> None:
        self._plugins.append(plugin)

    def load_entrypoints(self, group: str = "pynetscope.plugins") -> None:
        for entrypoint in metadata.entry_points(group=group):
            self.register(entrypoint.load()())

    def pre_request(self, context: RequestContext) -> RequestContext:
        current = context
        for plugin in self._plugins:
            hook = getattr(plugin, "pre_request", None)
            if hook:
                current = hook(current)
        return current

    def post_request(self, record: RequestRecord) -> RequestRecord:
        current = record
        for plugin in self._plugins:
            hook = getattr(plugin, "post_request", None)
            if hook:
                current = hook(current)
        return current
