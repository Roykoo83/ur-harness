"""C2 hash-based change event generation."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Mapping

from rpbd.core.timeutil import parse_datetime

from .record import Record, payload_dict

EventType = str


@dataclass(slots=True)
class ChangeEvent:
    event_id: str
    event_type: EventType
    record_id: str
    record_type: str
    project_id: str | None
    detected_at: str
    prev_hash: str | None
    curr_hash: str | None
    changed_fields: list[str]
    significance: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _changed_fields(previous: Record, current: Record) -> list[str]:
    before, after = payload_dict(previous), payload_dict(current)
    return sorted(key for key in before.keys() | after.keys() if before.get(key) != after.get(key))


def _temporary(record: Record) -> bool:
    payload = payload_dict(record)
    name = str(payload.get("name") or payload.get("rel_path") or "")
    return name.startswith("~$") or name.endswith(".tmp") or name.endswith(".DS_Store")


def _deadline_related(record: Record, fields: list[str]) -> bool:
    payload = payload_dict(record)
    text = " ".join(str(payload.get(key, "")) for key in ("name", "rel_path", "summary", "category_label"))
    return bool({"due", "period_end"} & set(fields)) or any(word in text.lower() for word in ("마감", "제출", "deadline", "due"))


def significance(
    event_type: str,
    current: Record,
    previous: Record | None = None,
    changed_fields: list[str] | None = None,
) -> str:
    fields = changed_fields or []
    if event_type == "unavailable" or _temporary(current):
        return "low"
    payload = payload_dict(current)
    if current.record_type == "signal" and event_type == "added":
        return "high"
    if current.record_type == "project" and "phase" in fields:
        return "high"
    if current.record_type == "document":
        if event_type == "added" and payload.get("category") in {"contract", "admin"}:
            return "high"
        if event_type == "modified" and _deadline_related(current, fields):
            return "high"
        if fields == ["size_bytes"] and previous is not None:
            before = payload_dict(previous).get("size_bytes")
            after = payload.get("size_bytes")
            if isinstance(before, int) and isinstance(after, int):
                threshold = max(4096, int(max(before, 1) * 0.01))
                if abs(after - before) <= threshold:
                    return "low"
        if event_type in {"added", "modified", "reappeared"}:
            return "normal"
    return "normal"


def _event(
    event_type: str,
    detected_at: str,
    previous: Record | None,
    current: Record | None,
) -> ChangeEvent:
    record = current or previous
    assert record is not None
    fields = _changed_fields(previous, current) if event_type == "modified" and previous and current else []
    payload = payload_dict(record)
    stamp = parse_datetime(detected_at).strftime("%Y-%m-%dT%H%M")
    return ChangeEvent(
        event_id=f"chg:{stamp}:{record.record_id}",
        event_type=event_type,
        record_id=record.record_id,
        record_type=record.record_type,
        project_id=payload.get("project_id"),
        detected_at=detected_at,
        prev_hash=previous.content_hash if previous else None,
        curr_hash=current.content_hash if current else None,
        changed_fields=fields,
        significance=significance(event_type, record, previous, fields),
    )


def detect_changes(
    previous: Mapping[str, Record], current: Mapping[str, Record], detected_at: str
) -> list[ChangeEvent]:
    """Compare current records with the previous state using content hashes."""
    events: list[ChangeEvent] = []
    for record_id in sorted(previous.keys() | current.keys()):
        before, after = previous.get(record_id), current.get(record_id)
        if after is None:
            events.append(_event("removed", detected_at, before, None))
        elif after.status == "unavailable":
            if before is None or before.status != "unavailable" or before.content_hash != after.content_hash:
                events.append(_event("unavailable", detected_at, before, after))
        elif before is None:
            events.append(_event("added", detected_at, None, after))
        elif before.status in {"removed", "unavailable"} and after.status == "present":
            events.append(_event("reappeared", detected_at, before, after))
        elif before.content_hash != after.content_hash:
            events.append(_event("modified", detected_at, before, after))
    return events


generate_changefeed = detect_changes
