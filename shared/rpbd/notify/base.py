"""Notification protocol and transport-neutral value objects."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping, Protocol


@dataclass(frozen=True, slots=True)
class Message:
    """A transport-neutral notification message."""

    text: str
    title: str | None = None
    metadata: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class Target:
    """A configured delivery destination, such as a room or recipient."""

    channel: str | None = None
    recipient: str | None = None
    metadata: Mapping[str, Any] = field(default_factory=dict)

    @property
    def configured(self) -> bool:
        """Return whether at least one explicit destination was supplied."""

        return bool(self.channel or self.recipient)


@dataclass(frozen=True, slots=True)
class SendResult:
    """A non-throwing notification delivery outcome."""

    sent: bool
    channel: str
    status: str
    attempts: int = 0
    detail: str | None = None
    external_id: str | None = None

    @property
    def success(self) -> bool:
        """Provide a readable alias for callers that prefer success semantics."""

        return self.sent


class Notifier(Protocol):
    """C5.5 external notification gate."""

    channel: str

    def send(
        self,
        msg: Message,
        target: Target,
        *,
        dry_run: bool = True,
    ) -> SendResult:
        """Send or safely skip a message without interrupting the pipeline."""

        ...
