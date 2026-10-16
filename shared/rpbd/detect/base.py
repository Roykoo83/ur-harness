"""Shared detector contracts and immutable finding values."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from hashlib import sha256
from typing import Iterable, Iterator, Literal, Mapping, Protocol, Sequence

from rpbd.ssot.changefeed import ChangeEvent
from rpbd.ssot.record import Record

Severity = Literal["urgent", "warn", "check"]


@dataclass(frozen=True, slots=True)
class Finding:
    finding_id: str
    severity: Severity
    project_id: str | None
    title: str
    detail: str
    action: str | None
    due: date | None
    evidence: list[str]

    def __post_init__(self) -> None:
        if self.severity not in {"urgent", "warn", "check"}:
            raise ValueError(f"unsupported severity: {self.severity}")
        evidence = [item.strip() for item in self.evidence if isinstance(item, str) and item.strip()]
        if not evidence:
            raise ValueError("Finding evidence must contain at least one record_id or event_id")
        object.__setattr__(self, "evidence", evidence)


@dataclass(frozen=True, slots=True)
class Snapshot:
    """Records visible to detectors at a specific decision date."""

    records: tuple[Record, ...]
    as_of: date

    @classmethod
    def from_records(
        cls,
        records: Iterable[Record],
        *,
        as_of: date | datetime | None = None,
    ) -> "Snapshot":
        decision_date = as_of.date() if isinstance(as_of, datetime) else as_of
        return cls(tuple(records), decision_date or date.today())


class Detector(Protocol):
    name: str

    def detect(self, changes: list[ChangeEvent], snapshot: Snapshot) -> Iterator[Finding]: ...


def ensure_snapshot(
    value: Snapshot | Sequence[Record] | Mapping[str, Record],
    *,
    as_of: date | datetime | None = None,
) -> Snapshot:
    if isinstance(value, Snapshot):
        return value
    records = value.values() if isinstance(value, Mapping) else value
    return Snapshot.from_records(records, as_of=as_of)


def make_finding_id(detector: str, identity: str, kind: str) -> str:
    digest = sha256(f"{detector}\0{identity}\0{kind}".encode("utf-8")).hexdigest()[:16]
    return f"fnd:{detector}:{digest}"


__all__ = [
    "Detector",
    "Finding",
    "Severity",
    "Snapshot",
    "ensure_snapshot",
    "make_finding_id",
]
