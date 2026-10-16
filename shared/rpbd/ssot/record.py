"""C1 record envelope and payload types."""

from __future__ import annotations

import re
import unicodedata
from dataclasses import asdict, dataclass, field
from typing import Any, Literal, Mapping

RecordType = Literal["document", "project", "organization", "person", "agency", "signal"]
HashBasis = Literal["content", "metadata", "fields"]
Provenance = Literal["ledger", "observed", "inferred"]
RecordStatus = Literal["present", "unavailable", "removed"]


def normalize_name(value: str) -> str:
    return re.sub(r"\s+", " ", unicodedata.normalize("NFC", value)).strip()


def make_record_id(record_type: RecordType, **identity: str | None) -> str:
    """Create a stable C1.4 identifier."""
    if record_type == "document":
        project_id = identity.get("project_id")
        rel_path = identity.get("rel_path")
        if not project_id or rel_path is None:
            raise ValueError("document requires project_id and rel_path")
        return f"doc:{project_id}:{rel_path.replace(':', '%3A')}"
    if record_type == "project":
        value = identity.get("project_id")
        prefix = "prj"
    elif record_type == "organization":
        org_id = identity.get("org_id")
        if org_id:
            value = org_id.replace("-", "")
            if not (len(value) == 10 and value.isdigit()):
                raise ValueError("org_id must be 10 digits")
            return f"org:{value}"
        name = identity.get("name")
        if not name:
            raise ValueError("organization requires org_id or name")
        return f"org:name:{normalize_name(name)}"
    elif record_type == "agency":
        value = identity.get("name")
        if not value:
            raise ValueError("agency requires name")
        return f"agy:{normalize_name(value)}"
    elif record_type == "person":
        value = identity.get("person_id")
        prefix = "psn"
    elif record_type == "signal":
        value = identity.get("signal_id")
        prefix = "sig"
    else:
        raise ValueError(f"unsupported record_type: {record_type}")
    if not value:
        raise ValueError(f"{record_type} identifier is required")
    return f"{prefix}:{value}"


create_record_id = make_record_id


Sensitivity = Literal["high", "medium", "normal"]


@dataclass(frozen=True, slots=True)
class Source:
    connector: Literal["drive", "nworks", "manual", "ledger"]
    root: str
    path: str


@dataclass(slots=True)
class DocumentPayload:
    project_id: str
    name: str
    rel_path: str
    ext: str
    size_bytes: int
    modified: str
    category: Literal["perform", "budget_exec", "budget_reg", "contract", "admin", "plan", "other"]
    category_label: str
    stage: str
    text_extracted: bool = False
    text_path: str | None = None
    parse_error: str | None = None


@dataclass(slots=True)
class ProjectPayload:
    project_id: str
    name_official: str
    name_short: str
    phase: Literal["active", "applying", "closed", "unknown"]
    period_start: str | None = None
    period_end: str | None = None
    ministry: str | None = None
    agency: str | None = None
    folder_path: str | None = None
    file_count: int = 0
    total_bytes: int = 0
    last_activity: str | None = None
    project_type: Literal["rnd", "etc", "ra"] | None = None
    stage: str | None = None
    stage_updated: str | None = None
    stage_history: list[dict[str, Any]] = field(default_factory=list)
    responsible_pm: str | None = None
    working_pm: str | None = None


@dataclass(slots=True)
class SignalPayload:
    signal_id: str
    received: str
    from_org: str
    project_id: str | None
    kind: str
    summary: str
    link: str | None = None
    due: str | None = None
    state: str = "확인"


@dataclass(slots=True)
class OrganizationPayload:
    name: str
    org_id: str | None = None


@dataclass(slots=True)
class AgencyPayload:
    name: str
    org_id: str | None = None


@dataclass(slots=True)
class PersonPayload:
    person_id: str
    name: str
    employment_status: Literal["active", "resigned", "planned-exit", "leave"] | None = None


Payload = DocumentPayload | ProjectPayload | SignalPayload | OrganizationPayload | AgencyPayload | PersonPayload | dict[str, Any]


_PAYLOAD_TYPES = {
    "document": DocumentPayload,
    "project": ProjectPayload,
    "signal": SignalPayload,
    "organization": OrganizationPayload,
    "agency": AgencyPayload,
    "person": PersonPayload,
}


@dataclass(slots=True)
class Record:
    record_id: str
    record_type: RecordType
    schema_version: int
    content_hash: str
    hash_basis: HashBasis
    observed_at: str
    source: Source
    provenance: Provenance
    status: RecordStatus
    # C1.7 (v1.2). Defaults to normal so pre-v1.2 records stay loadable;
    # collectors classify explicitly and the validator rejects unknown values.
    sensitivity: Sensitivity = "normal"
    payload: Payload = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "Record":
        data = dict(value)
        source = data.get("source")
        if not isinstance(source, Source):
            data["source"] = Source(**source)
        payload = data.get("payload", {})
        payload_type = _PAYLOAD_TYPES.get(data.get("record_type"))
        if payload_type and isinstance(payload, Mapping):
            try:
                data["payload"] = payload_type(**payload)
            except TypeError:
                data["payload"] = dict(payload)
        return cls(**data)


def payload_dict(record: Record) -> dict[str, Any]:
    payload = record.payload
    return asdict(payload) if hasattr(payload, "__dataclass_fields__") else dict(payload)
