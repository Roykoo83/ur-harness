"""Append-only JSONL SSOT storage."""

from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path
from typing import Iterable, Iterator

from rpbd.core.timeutil import parse_datetime

from .record import Record
from .validate import ValidationResult, validate_records


class RecordStore:
    def __init__(self, data_dir: str | Path) -> None:
        self.data_dir = Path(data_dir)
        self.records_dir = self.data_dir / "records"

    def path_for(self, record: Record) -> Path:
        date = parse_datetime(record.observed_at).date().isoformat()
        return self.records_dir / record.record_type / f"{date}.jsonl"

    def iter_records(self, record_type: str | None = None) -> Iterator[Record]:
        base = self.records_dir / record_type if record_type else self.records_dir
        if not base.exists():
            return
        for path in sorted(base.rglob("*.jsonl")):
            with path.open(encoding="utf-8") as stream:
                for line in stream:
                    if line.strip():
                        yield Record.from_dict(json.loads(line))

    def append(self, records: Iterable[Record]) -> ValidationResult:
        result = validate_records(records, self.iter_records())
        grouped: dict[Path, list[Record]] = defaultdict(list)
        for record in result.accepted:
            grouped[self.path_for(record)].append(record)
        for path, items in grouped.items():
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("a", encoding="utf-8", newline="\n") as stream:
                for record in items:
                    stream.write(json.dumps(record.to_dict(), ensure_ascii=False, separators=(",", ":")))
                    stream.write("\n")
        return result

    def current(self, record_type: str | None = None) -> dict[str, Record]:
        latest: dict[str, Record] = {}
        for record in self.iter_records(record_type):
            prior = latest.get(record.record_id)
            if prior is None or parse_datetime(record.observed_at) > parse_datetime(prior.observed_at):
                latest[record.record_id] = record
        return latest

    def get(self, record_id: str) -> Record | None:
        return self.current().get(record_id)

    latest = current
