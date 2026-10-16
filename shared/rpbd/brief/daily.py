"""Build daily brief data from SSOT records and change events."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from typing import Iterable, Sequence

from rpbd.detect import DEFAULT_DETECTORS, Detector, Finding, Snapshot
from rpbd.detect.deadline import project_deadline
from rpbd.ssot.changefeed import ChangeEvent
from rpbd.ssot.merge import merge_records
from rpbd.ssot.record import Record
from rpbd.ssot.store import RecordStore

KST = timezone(timedelta(hours=9))
_SEVERITY_ORDER = {"urgent": 0, "warn": 1, "check": 2}


@dataclass(frozen=True, slots=True)
class ApplicationItem:
    project_id: str
    name: str
    due: date | None
    ministry: str | None
    evidence: list[str]


@dataclass(frozen=True, slots=True)
class DailyBrief:
    generated_at: datetime
    source_summary: str
    active_count: int
    applying_count: int
    findings: tuple[Finding, ...]
    applications: tuple[ApplicationItem, ...]
    project_names: dict[str, str]
    project_phases: dict[str, str]

    @property
    def main_findings(self) -> tuple[Finding, ...]:
        return tuple(
            finding
            for finding in self.findings
            if self.project_phases.get(finding.project_id or "") != "applying"
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "generated_at": self.generated_at.isoformat(),
            "source_summary": self.source_summary,
            "active_count": self.active_count,
            "applying_count": self.applying_count,
            "findings": [asdict(item) for item in self.findings],
            "applications": [asdict(item) for item in self.applications],
            "project_names": self.project_names,
            "project_phases": self.project_phases,
        }


def _generated_at(value: datetime | date | None) -> datetime:
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=KST)
    if isinstance(value, date):
        return datetime.combine(value, time.min, tzinfo=KST)
    return datetime.now(KST)


def build_daily_brief(
    records: Iterable[Record],
    changes: Iterable[ChangeEvent] = (),
    *,
    generated_at: datetime | date | None = None,
    detectors: Sequence[Detector] = DEFAULT_DETECTORS,
) -> DailyBrief:
    generated = _generated_at(generated_at)
    record_list = list(records)
    change_list = list(changes)
    snapshot = Snapshot.from_records(record_list, as_of=generated)
    findings = [
        finding
        for detector in detectors
        for finding in detector.detect(change_list, snapshot)
    ]
    findings.sort(
        key=lambda item: (
            _SEVERITY_ORDER[item.severity],
            item.due or date.max,
            item.project_id or "",
            item.finding_id,
        )
    )

    projects = merge_records(record for record in record_list if record.record_type == "project")
    project_names: dict[str, str] = {}
    project_phases: dict[str, str] = {}
    applications: list[ApplicationItem] = []
    active_count = 0
    for record_id, project in sorted(projects.items()):
        if project.get("status") != "present":
            continue
        payload = project["payload"]
        project_id = str(payload.get("project_id") or record_id.removeprefix("prj:"))
        name = str(payload.get("name_short") or payload.get("name_official") or project_id)
        phase = str(payload.get("phase") or "unknown")
        project_names[project_id] = name
        project_phases[project_id] = phase
        if phase == "active":
            active_count += 1
        elif phase == "applying":
            applications.append(
                ApplicationItem(
                    project_id=project_id,
                    name=name,
                    due=project_deadline(project, generated.date()),
                    ministry=payload.get("ministry"),
                    evidence=[record_id],
                )
            )
    applications.sort(key=lambda item: (item.due or date.max, item.project_id))

    ledger_count = sum(
        1
        for record in record_list
        if record.record_type == "project" and record.provenance == "ledger"
    )
    signal_count = len({record.record_id for record in record_list if record.record_type == "signal"})
    source_summary = (
        f"SSOT 원장 레코드 {ledger_count}건 · 신호 {signal_count}건 · "
        f"스캔 {generated:%Y-%m-%d %H:%M}"
    )
    return DailyBrief(
        generated_at=generated,
        source_summary=source_summary,
        active_count=active_count,
        applying_count=len(applications),
        findings=tuple(findings),
        applications=tuple(applications),
        project_names=project_names,
        project_phases=project_phases,
    )


def load_change_events(data_dir: str | Path, on_date: date) -> list[ChangeEvent]:
    path = Path(data_dir) / "changes" / f"{on_date.isoformat()}.jsonl"
    if not path.exists():
        return []
    events: list[ChangeEvent] = []
    with path.open(encoding="utf-8") as stream:
        for line in stream:
            if line.strip():
                events.append(ChangeEvent(**json.loads(line)))
    return events


def generate_daily_brief(
    data_dir: str | Path = "data",
    *,
    generated_at: datetime | date | None = None,
    detectors: Sequence[Detector] = DEFAULT_DETECTORS,
) -> DailyBrief:
    generated = _generated_at(generated_at)
    store = RecordStore(data_dir)
    changes = load_change_events(data_dir, generated.date())
    return build_daily_brief(
        store.iter_records(),
        changes,
        generated_at=generated,
        detectors=detectors,
    )


__all__ = [
    "ApplicationItem",
    "DailyBrief",
    "build_daily_brief",
    "generate_daily_brief",
    "load_change_events",
]
