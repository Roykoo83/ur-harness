"""Deadline findings based on provenance-merged project records."""

from __future__ import annotations

import calendar
import re
from datetime import date
from typing import Any, Iterator

from rpbd.ssot.changefeed import ChangeEvent
from rpbd.ssot.merge import merge_records

from .base import Finding, Snapshot, make_finding_id

_RANGE_END = re.compile(r"\d{4}[.-]\d{1,2}\s*[-~]\s*(\d{4})[.-](\d{1,2})")
_YEAR_MONTH = re.compile(r"(?<!\d)(\d{4})[.-](\d{1,2})(?!\d)")
_MONTH_DAY_DUE = re.compile(r"(?<!\d)(\d{1,2})[.-](\d{1,2})[^)]{0,12}(?:까지|마감)")


def folder_deadline(folder_path: str | None, as_of: date) -> date | None:
    """Infer a fallback deadline from common RPBD folder naming patterns."""
    if not folder_path:
        return None
    ranged = _RANGE_END.search(folder_path)
    if ranged:
        year, month = map(int, ranged.groups())
        return date(year, month, calendar.monthrange(year, month)[1])
    due = _MONTH_DAY_DUE.search(folder_path)
    if due:
        month, day = map(int, due.groups())
        try:
            return date(as_of.year, month, day)
        except ValueError:
            return None
    months = list(_YEAR_MONTH.finditer(folder_path))
    if months:
        year, month = map(int, months[-1].groups())
        return date(year, month, calendar.monthrange(year, month)[1])
    return None


def project_deadline(project: dict[str, Any], as_of: date) -> date | None:
    payload = project["payload"]
    asserted = payload.get("period_end")
    if asserted:
        try:
            return date.fromisoformat(str(asserted))
        except ValueError:
            return None
    return folder_deadline(payload.get("folder_path"), as_of)


class DeadlineDetector:
    name = "deadline"

    def detect(self, changes: list[ChangeEvent], snapshot: Snapshot) -> Iterator[Finding]:
        del changes
        projects = merge_records(
            record for record in snapshot.records if record.record_type == "project"
        )
        for record_id, project in sorted(projects.items()):
            if project.get("status") != "present":
                continue
            payload = project["payload"]
            deadline = project_deadline(project, snapshot.as_of)
            if deadline is None:
                continue
            days_left = (deadline - snapshot.as_of).days
            phase = payload.get("phase")
            applying = phase == "applying"
            if days_left < 0:
                severity = "urgent"
                title = "제출 마감 경과" if applying else "사업기간 종료"
                detail = f"기한 {abs(days_left)}일 경과."
                action = "결과 상태와 후속 조치를 확인"
                kind = "overdue"
            elif days_left <= 30:
                severity = "warn"
                title = "제출 마감 임박" if applying else "사업기간 종료 임박"
                detail = f"마감까지 {days_left}일 남음."
                action = "제출 준비 상태 확인" if applying else "정산·성과보고 준비 상태 확인"
                kind = "near"
            elif days_left <= 90 and not applying:
                severity = "check"
                title = "사업기간 종료 점검"
                detail = f"종료까지 {days_left}일 남음."
                action = "종료 일정과 산출물 계획 확인"
                kind = "upcoming"
            else:
                continue
            project_id = payload.get("project_id")
            yield Finding(
                finding_id=make_finding_id(self.name, record_id, kind),
                severity=severity,
                project_id=project_id,
                title=title,
                detail=detail,
                action=action,
                due=deadline,
                evidence=[record_id],
            )


__all__ = ["DeadlineDetector", "folder_deadline", "project_deadline"]
