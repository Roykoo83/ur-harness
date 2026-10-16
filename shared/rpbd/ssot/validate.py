"""C1.6 record validation."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Iterable, Mapping

from rpbd.core.timeutil import is_timezone_aware

from .record import Record, payload_dict

HASH_RE = re.compile(r"^sha256:[0-9a-fA-F]{64}$")
RECORD_TYPES = {"document", "project", "organization", "person", "agency", "signal"}
PROVENANCES = {"ledger", "observed", "inferred"}
STATUSES = {"present", "unavailable", "removed"}
HASH_BASES = {"content", "metadata", "fields"}
SENSITIVE_CLASSES = {"Credential", "Card", "BankAccount"}
SENSITIVE_RELATIONS = {"hasCredential", "paysWith", "depositsTo", "hasNationalResearcherId"}


@dataclass(slots=True)
class RejectedRecord:
    record: Record | Mapping[str, Any]
    reasons: list[str]

    @property
    def reason(self) -> str:
        return "; ".join(self.reasons)

    def to_dict(self) -> dict[str, Any]:
        body = self.record.to_dict() if isinstance(self.record, Record) else dict(self.record)
        return {"record": body, "reasons": self.reasons}


@dataclass(slots=True)
class ValidationResult:
    accepted: list[Record]
    rejected: list[RejectedRecord]


def _contains_sensitive(value: Any) -> bool:
    if isinstance(value, Mapping):
        for key, item in value.items():
            if key in SENSITIVE_RELATIONS or (
                isinstance(item, str) and item in SENSITIVE_CLASSES | SENSITIVE_RELATIONS
            ):
                return True
            if _contains_sensitive(item):
                return True
    elif isinstance(value, (list, tuple, set)):
        return any(_contains_sensitive(item) for item in value)
    return isinstance(value, str) and value in SENSITIVE_CLASSES | SENSITIVE_RELATIONS


SENSITIVITIES = frozenset({"high", "medium", "normal"})


def validate_record(record: Record) -> list[str]:
    errors: list[str] = []
    if record.record_type not in RECORD_TYPES:
        errors.append("record_type is not allowed")
    if record.schema_version != 1:
        errors.append("schema_version must be 1")
    if not HASH_RE.fullmatch(record.content_hash):
        errors.append("content_hash must be sha256:<64 hex>")
    if record.hash_basis not in HASH_BASES:
        errors.append("hash_basis is not allowed")
    if not is_timezone_aware(record.observed_at):
        errors.append("observed_at must be timezone-aware ISO8601")
    if record.provenance not in PROVENANCES:
        errors.append("provenance is not allowed")
    if record.status not in STATUSES:
        errors.append("status is not allowed")
    if getattr(record, "sensitivity", "normal") not in SENSITIVITIES:
        errors.append("sensitivity is not allowed")
    if _contains_sensitive(payload_dict(record)):
        errors.append("sensitive classes and relations are forbidden")
    return errors


def validate_records(
    records: Iterable[Record], existing: Iterable[Record] = ()
) -> ValidationResult:
    candidates = list(records)
    existing_records = list(existing)
    project_ids = {
        record.record_id[4:]
        for record in existing_records
        if record.record_type == "project" and record.record_id.startswith("prj:")
    }
    seen = {(record.record_id, record.observed_at) for record in existing_records}
    evaluated: list[tuple[Record, list[str]]] = []
    for record in candidates:
        errors = validate_record(record)
        # C1.6 v1.1: same-instant observations at different provenance tiers are
        # distinct assertions, not duplicates -- C1.3 merging depends on both surviving.
        key = (record.record_id, record.observed_at, record.provenance)
        if key in seen:
            errors.append("record_id + observed_at + provenance must be unique")
        seen.add(key)
        evaluated.append((record, errors))
    project_ids.update(
        record.record_id[4:]
        for record, errors in evaluated
        if not errors
        and record.record_type == "project"
        and record.record_id.startswith("prj:")
    )
    accepted: list[Record] = []
    rejected: list[RejectedRecord] = []
    for record, errors in evaluated:
        payload = payload_dict(record)
        project_id = payload.get("project_id")
        if project_id and record.record_type != "project" and project_id not in project_ids:
            errors.append(f"missing project reference: prj:{project_id}")
        if errors:
            rejected.append(RejectedRecord(record, errors))
        else:
            accepted.append(record)
    return ValidationResult(accepted, rejected)
