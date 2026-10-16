"""Approval-gated Kakao notification interface stub."""

from __future__ import annotations

import logging
from typing import Literal

from .base import Message, SendResult, Target

KakaoMode = Literal["kakaowork", "alimtalk", "memo"]


class KakaoNotifier:
    """Represent a Kakao choice without implementing any delivery transport."""

    channel = "kakao"

    def __init__(
        self,
        *,
        mode: KakaoMode = "alimtalk",
        enabled: bool = False,
        logger: logging.Logger | None = None,
    ) -> None:
        if mode not in {"kakaowork", "alimtalk", "memo"}:
            raise ValueError("mode must be 'kakaowork', 'alimtalk', or 'memo'")
        self.mode = mode
        self.enabled = enabled
        self.logger = logger or logging.getLogger("rpbd.notify.kakao")

    def send(
        self,
        msg: Message,
        target: Target,
        *,
        dry_run: bool = True,
    ) -> SendResult:
        """Always return a no-op result because Kakao adoption needs approval."""

        if dry_run:
            return SendResult(False, self.channel, "dry_run", detail="Delivery was not attempted.")
        if not self.enabled:
            return SendResult(
                False,
                self.channel,
                "disabled",
                detail="Set notify.enabled=true only after Kakao adoption is approved.",
            )
        if not target.configured:
            return SendResult(
                False,
                self.channel,
                "target_missing",
                detail="Configure an approved Kakao target before delivery.",
            )

        # KakaoWork requires company-wide workspace adoption and enrolled users.
        # Alimtalk requires business verification, an approved template, a reseller
        # contract, and per-message billing (plus optional SMS/LMS failover fees).
        # Memo requires per-user OAuth and can send only to that user's own chat;
        # it is unsuitable as a shared company notification channel.
        detail = (
            f"Kakao mode '{self.mode}' is approval-gated and intentionally has no "
            "delivery implementation. No message was sent."
        )
        self.logger.warning(detail)
        return SendResult(False, self.channel, "approval_required", detail=detail)

