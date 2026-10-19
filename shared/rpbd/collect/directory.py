"""NAVER Works directory as the personnel master.

운영자 2026-08-04: the directory is the current source of truth for who works here.
The HR spreadsheet is a point-in-time export (2026-07-22) whose org column had
already drifted -- it carried "퇴사" as if it were a company name. The directory
reflects account state today, so it supplies the person records and the
spreadsheet is demoted to project-allocation data.

Only currently provisioned accounts become people. Past leavers are not carried
over (운영자: keep only leavers occurring from now on); the source spreadsheet
stays in rawdata untouched, so nothing is destroyed -- the SSOT simply describes
the present staff.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, Iterator

from rpbd.collect.base import RunContext, get_iso_now, compute_fields_hash
from rpbd.collect.directory_match import (
    FORBIDDEN_FIELDS,
    extract_person,
    is_person,
)

# nworks-agent owns the API client; this project calls it rather than re-implementing.
_NWORKS_SCRIPTS = Path(__file__).resolve().parents[3] / "agents"


def _load_directory_users() -> list[dict[str, Any]]:
    if str(_NWORKS_SCRIPTS) not in sys.path:
        sys.path.insert(0, str(_NWORKS_SCRIPTS))
    from nworks.client import NworksClient  # noqa: PLC0415 - optional dependency
    from nworks.domains import directory_users  # noqa: PLC0415

    return list(directory_users(NworksClient()))


class DirectoryCollector:
    """Emit person records for currently provisioned employees."""

    name = "directory"

    def __init__(self, entries: list[dict[str, Any]] | None = None) -> None:
        self._entries = entries

    def collect(self, ctx: RunContext) -> Iterator[dict[str, Any]]:
        try:
            entries = self._entries if self._entries is not None else _load_directory_users()
        except Exception as err:
            if ctx and ctx.logger:
                ctx.logger.warning("directory unavailable: %s", err)
            # A directory outage must not look like "everyone left".
            yield self._unavailable_marker(f"{type(err).__name__}: {err}")
            return

        observed_at = get_iso_now()
        seq = 0
        for entry in entries:
            if not is_person(entry):
                continue
            person = extract_person(entry)
            seq += 1
            payload = {
                "person_id": f"UDL-{seq:03d}",
                "name": person.name,
                "email": person.email,
                "division": person.org_name,
                "position": person.position,
                "employment_status": "active",
                "join_date": person.hired_date,
                "resign_date": None,
                "organization": "{{ORG_NAME}}",
                "directory_user_id": person.user_id,
            }
            assert not any(f in payload for f in FORBIDDEN_FIELDS)
            yield {
                "record_id": f"psn:{payload['person_id']}",
                "record_type": "person",
                "schema_version": 1,
                "content_hash": compute_fields_hash(payload),
                "hash_basis": "fields",
                "observed_at": observed_at,
                "source": {"connector": "nworks", "root": "directory", "path": "/users"},
                "provenance": "ledger",
                "status": "present",
                "sensitivity": "medium",
                "payload": payload,
            }

    def _unavailable_marker(self, reason: str) -> dict[str, Any]:
        payload = {"name": "directory", "parse_error": reason}
        return {
            "record_id": "psn:DIRECTORY-STATUS",
            "record_type": "person",
            "schema_version": 1,
            "content_hash": compute_fields_hash(payload),
            "hash_basis": "fields",
            "observed_at": get_iso_now(),
            "source": {"connector": "nworks", "root": "directory", "path": "/users"},
            "provenance": "observed",
            "status": "unavailable",
            "sensitivity": "normal",
            "payload": payload,
        }


__all__ = ["DirectoryCollector"]
