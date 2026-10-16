"""Runtime registry for RPBD service plugins."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal


PluginKind = Literal["collector", "detector", "notifier"]


@dataclass
class PluginRegistry:
    _plugins: dict[str, dict[str, Any]] = field(
        default_factory=lambda: {"collector": {}, "detector": {}, "notifier": {}},
        init=False,
    )

    def register(self, kind: PluginKind, name: str, plugin: Any) -> Any:
        if kind not in self._plugins:
            raise ValueError(f"unknown plugin kind: {kind}")
        if not name:
            raise ValueError("plugin name must not be empty")
        if name in self._plugins[kind]:
            raise ValueError(f"{kind} already registered: {name}")
        self._plugins[kind][name] = plugin
        return plugin

    def get(self, kind: PluginKind, name: str) -> Any:
        try:
            return self._plugins[kind][name]
        except KeyError as exc:
            raise KeyError(f"{kind} plugin not found: {name}") from exc

    def names(self, kind: PluginKind) -> tuple[str, ...]:
        if kind not in self._plugins:
            raise ValueError(f"unknown plugin kind: {kind}")
        return tuple(self._plugins[kind])

    def register_collector(self, plugin: Any, name: str | None = None) -> Any:
        return self.register("collector", name or plugin.name, plugin)

    def register_detector(self, plugin: Any, name: str | None = None) -> Any:
        return self.register("detector", name or plugin.name, plugin)

    def register_notifier(self, plugin: Any, name: str | None = None) -> Any:
        return self.register("notifier", name or plugin.channel, plugin)

    def collector(self, name: str) -> Any:
        return self.get("collector", name)

    def detector(self, name: str) -> Any:
        return self.get("detector", name)

    def notifier(self, name: str) -> Any:
        return self.get("notifier", name)
