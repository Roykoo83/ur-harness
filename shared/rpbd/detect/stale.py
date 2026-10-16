"""Project activity-state findings."""

from __future__ import annotations

from datetime import date
from typing import Iterator, Literal

from rpbd.ssot.changefeed import ChangeEvent
from rpbd.ssot.merge import merge_records

from .base import Finding, Snapshot, make_finding_id

ActivityState = Literal["active", "normal", "stale", "unknown"]


def activity_state(last_activity: str | date | None, as_of: date) -> tuple[ActivityState, int | None]:
    if last_activity is None:
        return "unknown", None
    try:
        activity_date = last_activity if isinstance(last_activity, date) else date.fromisoformat(last_activity)
    except (TypeError, ValueError):
        return "unknown", None
    days_since = max(0, (as_of - activity_date).days)
    if days_since <= 7:
        return "active", days_since
    if days_since > 21:
        return "stale", days_since
    return "normal", days_since


class StaleDetector:
    name = "stale"

    def detect(self, changes: list[ChangeEvent], snapshot: Snapshot) -> Iterator[Finding]:
        del changes
        projects = merge_records(
            record for record in snapshot.records if record.record_type == "project"
        )
        for record_id, project in sorted(projects.items()):
            if project.get("status") != "present":
                continue
            payload = project["payload"]
            if payload.get("phase") != "active":
                continue
            state, days_since = activity_state(payload.get("last_activity"), snapshot.as_of)
            if state == "stale":
                yield Finding(
                    finding_id=make_finding_id(self.name, record_id, state),
                    severity="warn",
                    project_id=payload.get("project_id"),
                    title="활동 정체",
                    detail=f"최근 파일 활동 {days_since}일 전.",
                    action="진행상황 점검",
                    due=None,
                    evidence=[record_id],
                )
            # A healthy "active" project is a state, not an action item. It is exposed
            # through projects.json activity.state (C6.5); putting it in the brief would
            # bury the items that actually need someone to act (C2.3).


__all__ = ["ActivityState", "StaleDetector", "activity_state"]
