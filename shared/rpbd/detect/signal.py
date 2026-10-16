"""Findings for newly arrived and still-unhandled signals."""

from __future__ import annotations

from typing import Any, Iterator

from rpbd.ssot.changefeed import ChangeEvent
from rpbd.ssot.merge import merge_records

from .base import Finding, Snapshot, make_finding_id

_HANDLED_STATES = {"완료", "처리", "처리완료", "종결", "done", "closed", "resolved"}


def signal_is_unhandled(payload: dict[str, Any]) -> bool:
    state = str(payload.get("state") or "").strip().lower()
    return state not in _HANDLED_STATES


def _detail(payload: dict[str, Any]) -> str:
    kind = payload.get("kind") or "신호"
    summary = payload.get("summary") or "내용 없음"
    received = payload.get("received")
    sender = payload.get("from_org")
    suffix = ", ".join(str(value) for value in (received, sender) if value)
    return f"[{kind}] {summary}" + (f" ({suffix})" if suffix else "")


class SignalDetector:
    name = "signal"

    def detect(self, changes: list[ChangeEvent], snapshot: Snapshot) -> Iterator[Finding]:
        signals = merge_records(
            record for record in snapshot.records if record.record_type == "signal"
        )
        new_events = {
            event.record_id: event
            for event in changes
            if event.record_type == "signal" and event.event_type == "added"
        }
        for record_id, signal in sorted(signals.items()):
            if signal.get("status") != "present":
                continue
            payload = signal["payload"]
            event = new_events.get(record_id)
            if event is not None:
                title = "신규 신호 유입"
                kind = "new"
                evidence = [event.event_id, record_id]
            elif signal_is_unhandled(payload):
                title = "미처리 신호"
                kind = "unhandled"
                evidence = [record_id]
            else:
                continue
            due = None
            if payload.get("due"):
                try:
                    from datetime import date

                    due = date.fromisoformat(str(payload["due"]))
                except ValueError:
                    pass
            yield Finding(
                finding_id=make_finding_id(self.name, record_id, kind),
                severity="warn",
                project_id=payload.get("project_id"),
                title=title,
                detail=_detail(payload),
                action="담당자와 처리 상태 확인",
                due=due,
                evidence=evidence,
            )


__all__ = ["SignalDetector", "signal_is_unhandled"]
