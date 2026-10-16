"""Synchronous internal event bus with isolated handler failures."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
import logging
from typing import Any, Callable, Iterable


SERVICE_EVENT_CHAIN = (
    "collection.completed",
    "detection.completed",
    "brief.completed",
    "notification.completed",
)


@dataclass(frozen=True)
class Event:
    name: str
    payload: Any = None
    run_id: str | None = None


@dataclass(frozen=True)
class HandlerResult:
    event: Event
    handler: Callable[[Event], Any]
    value: Any = None
    error: Exception | None = None

    @property
    def succeeded(self) -> bool:
        return self.error is None


@dataclass
class EventBus:
    logger: logging.Logger = field(default_factory=lambda: logging.getLogger("rpbd.bus"))
    _handlers: dict[str, list[Callable[[Event], Any]]] = field(
        default_factory=lambda: defaultdict(list), init=False
    )

    def subscribe(self, event_name: str, handler: Callable[[Event], Any]) -> Callable[[Event], Any]:
        self._handlers[event_name].append(handler)
        return handler

    on = subscribe

    def publish(self, event: Event | str, payload: Any = None, *, run_id: str | None = None) -> list[HandlerResult]:
        current = event if isinstance(event, Event) else Event(event, payload, run_id)
        results: list[HandlerResult] = []
        for handler in tuple(self._handlers.get(current.name, ())):
            try:
                value = handler(current)
            except Exception as exc:
                self.logger.exception(
                    "event handler failed: event=%s handler=%s",
                    current.name,
                    getattr(handler, "__name__", type(handler).__name__),
                )
                results.append(HandlerResult(current, handler, error=exc))
            else:
                results.append(HandlerResult(current, handler, value=value))
        return results

    emit = publish

    def publish_chain(
        self,
        payload: Any,
        *,
        run_id: str | None = None,
        event_names: Iterable[str] = SERVICE_EVENT_CHAIN,
    ) -> list[HandlerResult]:
        """Publish service stages in order, regardless of individual failures."""
        results: list[HandlerResult] = []
        current_payload = payload
        for name in event_names:
            stage_results = self.publish(name, current_payload, run_id=run_id)
            results.extend(stage_results)
            successful_values = [item.value for item in stage_results if item.succeeded and item.value is not None]
            if successful_values:
                current_payload = successful_values[-1]
        return results
