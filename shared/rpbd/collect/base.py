"""Base protocol and data structures for RPBD collectors.
"""

from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
import hashlib
import json
import logging
from typing import Any, Iterator, Protocol, Optional, Literal

KST = timezone(timedelta(hours=9))


@dataclass
class RunContext:
    """Execution context for collector runs."""
    run_id: str
    started_at: datetime
    logger: logging.Logger
    dry_run: bool = True
    prev_snapshot: dict[str, str] = field(default_factory=dict)


class Collector(Protocol):
    """Protocol defining the interface for all RPBD collectors."""
    name: str

    def collect(self, ctx: RunContext) -> Iterator[dict[str, Any]]:
        """Collect records from source and yield C1 formatted record dictionaries."""
        ...


def get_iso_now() -> str:
    """Return current ISO8601 formatted timestamp with KST (+09:00) timezone."""
    return datetime.now(KST).isoformat()


def compute_fields_hash(payload: dict[str, Any]) -> str:
    """Compute sha256 hash for payload dictionary using sorted keys."""
    raw = json.dumps(payload, sort_keys=True, ensure_ascii=False)
    return "sha256:" + hashlib.sha256(raw.encode("utf-8")).hexdigest()


def compute_metadata_hash(rel_path: str, size: int, modified: str) -> str:
    """Compute sha256 metadata hash for document records."""
    raw = f"{rel_path}:{size}:{modified}"
    return "sha256:" + hashlib.sha256(raw.encode("utf-8")).hexdigest()


def make_record(
    record_id: str,
    record_type: Literal["document", "project", "organization", "person", "agency", "signal"],
    payload: dict[str, Any],
    source: dict[str, str],
    provenance: Literal["ledger", "observed", "inferred"],
    status: Literal["present", "unavailable", "removed"] = "present",
    hash_basis: Literal["content", "metadata", "fields"] = "fields",
    content_hash: Optional[str] = None,
    observed_at: Optional[str] = None,
    schema_version: int = 1,
    sensitivity: Optional[Literal["high", "medium", "normal"]] = None,
) -> dict[str, Any]:
    """Construct a C1 compliant record dictionary."""
    if sensitivity is None:
        # C1.7: every document is graded. Classification lives in one place so a
        # new collector cannot forget it and quietly emit an ungraded record.
        from rpbd.ssot.sensitivity import classify

        sensitivity = (
            classify(
                str(payload.get("name") or ""),
                payload.get("category"),
                str(payload.get("rel_path") or ""),
            )
            if record_type == "document"
            else "normal"
        )
    if observed_at is None:
        observed_at = get_iso_now()

    if content_hash is None:
        if hash_basis == "fields":
            content_hash = compute_fields_hash(payload)
        elif hash_basis == "metadata":
            rel_path = payload.get("rel_path", "")
            size = payload.get("size_bytes", 0)
            modified = payload.get("modified", "")
            content_hash = compute_metadata_hash(rel_path, size, modified)
        else:
            content_hash = "sha256:" + "0" * 64

    return {
        "record_id": record_id,
        "record_type": record_type,
        "sensitivity": sensitivity,
        "schema_version": schema_version,
        "content_hash": content_hash,
        "hash_basis": hash_basis,
        "observed_at": observed_at,
        "source": source,
        "provenance": provenance,
        "status": status,
        "payload": payload,
    }
