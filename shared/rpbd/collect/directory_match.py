"""Match HR records to NAVER Works directory entries to recover company email.

The HR spreadsheet has no email column, which leaves no join key between the
personnel master and collected mail. The directory has one. It also carries
birthday, cellPhone and privateEmail -- exclusion_list.md forbids all three, so
this module reads a fixed allowlist of fields and never touches the rest.

Matching is name-based and therefore fallible, so nothing is written
automatically: the output is a review file for a person to confirm.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping

# Only these directory fields may be read. Anything absent here is never copied.
ALLOWED_FIELDS = ("userId", "userName", "email", "organizations", "employeeNumber", "hiredDate")

# Explicitly forbidden by exclusion_list.md §3 (personal contact / identifiers).
FORBIDDEN_FIELDS = ("birthday", "cellPhone", "privateEmail", "telephone", "location")

# Directory entries that are not people: shared mailboxes, service accounts, vendors.
_NON_PERSON_MARKERS = (
    "관리자", "계정", "github", "aws", "msp", "urban_work", "대표 메일",
    "(주)", "주식회사", "it", "admin", "test", "bot",
)


@dataclass
class DirectoryPerson:
    user_id: str
    name: str
    email: str
    org_name: str | None = None
    position: str | None = None
    employee_number: str | None = None
    hired_date: str | None = None


@dataclass
class MatchResult:
    matched: list[dict[str, Any]] = field(default_factory=list)
    ambiguous: list[dict[str, Any]] = field(default_factory=list)
    hr_only: list[dict[str, Any]] = field(default_factory=list)
    directory_only: list[dict[str, Any]] = field(default_factory=list)
    skipped_non_person: list[str] = field(default_factory=list)


def normalize_name(value: Any) -> str:
    """Collapse spacing and width variants so '홍 길동' and '홍길동' compare equal.

    The directory returns userName as {'lastName': ..., 'firstName': ...}, and
    splits Korean names inconsistently -- some rows carry the whole name in
    lastName, others split it. Joining both parts handles either shape.
    """
    if isinstance(value, Mapping):
        value = f"{value.get('lastName') or ''}{value.get('firstName') or ''}"
    text = unicodedata.normalize("NFKC", str(value or ""))
    return re.sub(r"\s+", "", text).strip()


def is_person(entry: Mapping[str, Any]) -> bool:
    """Reject shared mailboxes and service accounts.

    A real employee has a company email and a personal-looking name. Service
    accounts in this tenant are named GITHUB, AWS, urban_work_01, 관리자계정 and
    similar, so a marker list plus a length check separates them reliably.
    """
    name = normalize_name(entry.get("userName"))
    if not name or not entry.get("email"):
        return False
    lowered = name.lower()
    if any(marker in lowered for marker in _NON_PERSON_MARKERS):
        return False
    # Korean personal names are 2-4 characters; longer entries are labels.
    if re.fullmatch(r"[가-힣]{2,4}", name):
        return True
    return False


def extract_person(entry: Mapping[str, Any]) -> DirectoryPerson:
    """Copy only the allowlisted fields out of a directory entry."""
    orgs = entry.get("organizations") or []
    org = orgs[0] if isinstance(orgs, list) and orgs else {}
    org_units = org.get("orgUnits") or []
    unit = org_units[0] if isinstance(org_units, list) and org_units else {}
    return DirectoryPerson(
        user_id=str(entry.get("userId") or ""),
        name=normalize_name(entry.get("userName")),
        email=str(entry.get("email") or ""),
        org_name=org.get("orgUnitName") or unit.get("orgUnitName"),
        position=unit.get("positionName") or org.get("positionName"),
        employee_number=entry.get("employeeNumber") or None,
        hired_date=entry.get("hiredDate") or None,
    )


def match(
    hr_people: Iterable[Mapping[str, Any]],
    directory_entries: Iterable[Mapping[str, Any]],
) -> MatchResult:
    """Match HR rows to directory people by normalized name.

    Names are not unique in principle, so a name hitting more than one directory
    entry is reported as ambiguous rather than resolved by guessing.
    """
    result = MatchResult()

    by_name: dict[str, list[DirectoryPerson]] = {}
    for entry in directory_entries:
        if not is_person(entry):
            label = normalize_name(entry.get("userName")) or "(이름 없음)"
            result.skipped_non_person.append(label)
            continue
        person = extract_person(entry)
        by_name.setdefault(person.name, []).append(person)

    seen: set[str] = set()
    for row in hr_people:
        hr_name = normalize_name(str(row.get("name") or ""))
        candidates = by_name.get(hr_name, [])
        base = {
            "person_id": row.get("person_id"),
            "hr_name": hr_name,
            "employment_status": row.get("employment_status"),
        }
        if len(candidates) == 1:
            person = candidates[0]
            seen.add(person.user_id)
            result.matched.append({
                **base,
                "email": person.email,
                "user_id": person.user_id,
                "org_name": person.org_name,
                "position": person.position,
            })
        elif len(candidates) > 1:
            for person in candidates:
                seen.add(person.user_id)
            result.ambiguous.append({
                **base,
                "candidates": [{"email": p.email, "user_id": p.user_id} for p in candidates],
            })
        else:
            result.hr_only.append(base)

    for people in by_name.values():
        for person in people:
            if person.user_id not in seen:
                result.directory_only.append({
                    "name": person.name,
                    "email": person.email,
                    "org_name": person.org_name,
                })
    return result


__all__ = [
    "ALLOWED_FIELDS",
    "FORBIDDEN_FIELDS",
    "DirectoryPerson",
    "MatchResult",
    "normalize_name",
    "is_person",
    "extract_person",
    "match",
]
