"""Field-level provenance merging."""

from __future__ import annotations

from collections import defaultdict
from typing import Any, Iterable

from rpbd.core.timeutil import parse_datetime

from .record import Record, payload_dict

RANK = {"inferred": 0, "observed": 1, "ledger": 2}


def merge_record(records: Iterable[Record]) -> dict[str, Any]:
    """Merge records for one identity and expose inferred fields."""
    candidates = list(records)
    if not candidates:
        raise ValueError("at least one record is required")
    ids = {record.record_id for record in candidates}
    if len(ids) != 1:
        raise ValueError("all records must have the same record_id")
    chosen: dict[str, tuple[Any, Record]] = {}
    for record in candidates:
        for field, value in payload_dict(record).items():
            prior = chosen.get(field)
            score = (RANK[record.provenance], parse_datetime(record.observed_at))
            if prior is None:
                chosen[field] = (value, record)
                continue
            # None means "unknown", not "asserted empty". A higher tier that does not
            # carry the field must not shadow a lower tier that actually observed it.
            prior_value, prior_record = prior
            if value is None and prior_value is not None:
                continue
            prior_score = (RANK[prior_record.provenance], parse_datetime(prior_record.observed_at))
            if prior_value is None and value is not None:
                chosen[field] = (value, record)
            elif score > prior_score:
                chosen[field] = (value, record)
    latest = max(candidates, key=lambda item: parse_datetime(item.observed_at))
    result = latest.to_dict()
    result["payload"] = {field: value for field, (value, _) in chosen.items()}
    result["field_provenance"] = {field: record.provenance for field, (_, record) in chosen.items()}
    result["needs_confirmation"] = sorted(
        field for field, (_, record) in chosen.items() if record.provenance == "inferred"
    )
    return result


def merge_records(records: Iterable[Record]) -> dict[str, dict[str, Any]]:
    grouped: dict[str, list[Record]] = defaultdict(list)
    for record in records:
        grouped[record.record_id].append(record)
    return {record_id: merge_record(items) for record_id, items in grouped.items()}
