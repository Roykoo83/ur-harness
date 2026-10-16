"""Thin, fail-open adapter for the separate nworks-agent project."""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

from .base import Message, SendResult, Target

NWorksSender = Callable[[Message, Target], Any]


class _DeliveryFailed(RuntimeError):
    """Internal signal used to retry a negative delegated result."""


class NWorksNotifier:
    """Delegate delivery to nworks-agent while enforcing the C5.5 gates."""

    channel = "nworks"

    def __init__(
        self,
        *,
        enabled: bool = False,
        sender: NWorksSender | None = None,
        logger: logging.Logger | None = None,
    ) -> None:
        self.enabled = enabled
        self._sender = sender
        self.logger = logger or logging.getLogger("rpbd.notify.nworks")

    def send(
        self,
        msg: Message,
        target: Target,
        *,
        dry_run: bool = True,
    ) -> SendResult:
        """Delegate at most twice and convert every failure into a result."""

        if dry_run:
            return SendResult(False, self.channel, "dry_run", detail="Delivery was not attempted.")
        if not self.enabled:
            return SendResult(
                False,
                self.channel,
                "disabled",
                detail="Set notify.enabled=true to permit delivery.",
            )
        if not target.configured:
            return SendResult(
                False,
                self.channel,
                "target_missing",
                detail="Configure a NAVER WORKS target channel or recipient.",
            )
        if self._sender is None:
            detail = (
                "NAVER WORKS delivery is unavailable: nworks-agent P0 app registration "
                "is not complete or its sender was not configured. No message was sent."
            )
            self.logger.warning(detail)
            return SendResult(False, self.channel, "not_configured", detail=detail)

        last_error: Exception | None = None
        for attempt in (1, 2):
            try:
                outcome = self._sender(msg, target)
                self._raise_for_failure(outcome)
                return self._success_result(outcome, attempt)
            except Exception as exc:  # The notification boundary must remain fail-open.
                last_error = exc
                if attempt == 1:
                    self.logger.warning("NAVER WORKS delivery failed; retrying once: %s", exc)

        detail = f"NAVER WORKS delivery failed after one retry: {last_error}"
        self.logger.error(detail)
        return SendResult(False, self.channel, "failed", attempts=2, detail=detail)

    @staticmethod
    def _raise_for_failure(outcome: Any) -> None:
        if outcome is False:
            raise _DeliveryFailed("nworks-agent returned False")
        if isinstance(outcome, SendResult) and not outcome.sent:
            raise _DeliveryFailed(outcome.detail or outcome.status)
        if isinstance(outcome, dict):
            if outcome.get("sent") is False or outcome.get("success") is False:
                raise _DeliveryFailed(str(outcome.get("detail") or "nworks-agent reported failure"))

    def _success_result(self, outcome: Any, attempt: int) -> SendResult:
        """Normalize the nworks-agent result without duplicating its transport."""

        if isinstance(outcome, SendResult):
            return SendResult(
                outcome.sent,
                self.channel,
                outcome.status,
                attempts=attempt,
                detail=outcome.detail,
                external_id=outcome.external_id,
            )
        external_id: str | None = None
        if isinstance(outcome, dict):
            value = outcome.get("message_id") or outcome.get("id")
            external_id = str(value) if value is not None else None
        elif isinstance(outcome, str):
            external_id = outcome
        return SendResult(True, self.channel, "sent", attempts=attempt, external_id=external_id)


NworksNotifier = NWorksNotifier
