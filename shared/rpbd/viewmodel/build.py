"""Build the C6 frontend view models from the append-only SSOT."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from collections import Counter, defaultdict
from datetime import date
from pathlib import Path
from typing import Any, Iterable, Mapping

# Runnable as a script without an install step, matching pipeline.py.
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from rpbd.core.timeutil import iso_now, parse_datetime
from rpbd.ssot.merge import merge_records
from rpbd.ssot.sensitivity import classify, mask_name
from rpbd.ssot.record import Record, payload_dict
from rpbd.ssot.store import RecordStore

_DECISION_KINDS = {"confirm", "choose", "act"}
_SEVERITIES = {"urgent", "warn", "check"}
_DATE_IN_NAME = re.compile(r"(?<!\d)(\d{1,2})[.\-/](\d{1,2})(?:[.\-/](\d{2,4}))?")
_SAFE_PROJECT_ID = re.compile(r"^[A-Za-z0-9_-]+$")


class DecisionValidationError(ValueError):
    """Raised when a decision violates the C6.4 contract."""


def validate_decision(decision: Mapping[str, Any]) -> None:
    """Reject invalid C6.4 decisions, especially decisions without evidence."""
    required = {
        "decision_id",
        "kind",
        "severity",
        "title",
        "detail",
        "owner",
        "due",
        "options",
        "targets",
        "evidence",
        "action_hint",
    }
    missing = sorted(required - decision.keys())
    if missing:
        raise DecisionValidationError(f"decision is missing fields: {', '.join(missing)}")
    if decision["kind"] not in _DECISION_KINDS:
        raise DecisionValidationError(f"invalid decision kind: {decision['kind']}")
    if decision["severity"] not in _SEVERITIES:
        raise DecisionValidationError(f"invalid decision severity: {decision['severity']}")
    evidence = decision["evidence"]
    if not isinstance(evidence, list) or not evidence or not all(
        isinstance(item, str) and item.strip() for item in evidence
    ):
        raise DecisionValidationError("decision evidence must be a non-empty string list")
    if not isinstance(decision["targets"], list) or not isinstance(decision["options"], list):
        raise DecisionValidationError("decision targets and options must be lists")
    if decision["kind"] == "choose" and not decision["options"]:
        raise DecisionValidationError("choose decisions require options")


def _safe_ref(record_id: str, grade: str) -> str:
    """Opaque reference for a masked document.

    Masking the name alone leaks nothing less: record_id embeds the full path
    ("doc:PRJ-N003:증빙서류/인건비/4월/4월급여명세서_홍길동.pdf"). The browser only
    needs a stable handle, so high-sensitivity rows get a digest instead.
    """
    if grade != "high":
        return record_id
    return "doc:masked:" + hashlib.sha256(record_id.encode("utf-8")).hexdigest()[:16]


def _display_name(payload: Mapping[str, Any], fallback: str) -> str:
    """Filename as shown in the browser.

    C1.7 masks high-sensitivity documents here rather than at collection: the
    ledger still records the real name for audit, but payroll and bank-statement
    filenames must not reach a page anyone on the team can open.
    """
    raw = payload.get("name") or payload.get("rel_path") or fallback
    grade = payload.get("sensitivity") or classify(
        str(raw), payload.get("category"), str(payload.get("rel_path") or "")
    )
    return mask_name(str(raw), grade, payload.get("category_label"))


def _json_lines(paths: Iterable[Path]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for path in sorted(paths):
        with path.open(encoding="utf-8") as stream:
            for line in stream:
                if line.strip():
                    rows.append(json.loads(line))
    return rows


def _latest_json(directory: Path) -> dict[str, Any]:
    paths = sorted(directory.glob("*.json")) if directory.exists() else []
    if not paths:
        return {}
    with paths[-1].open(encoding="utf-8") as stream:
        return json.load(stream)


def _date(value: Any) -> date | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        return date.fromisoformat(value[:10])
    except ValueError:
        return None


def _as_of(generated_at: str) -> date:
    return parse_datetime(generated_at).date()


def _activity(last: Any, today: date) -> dict[str, Any]:
    last_date = _date(last)
    if last_date is None:
        return {"last": None, "days_since": None, "state": "normal"}
    days_since = (today - last_date).days
    if days_since <= 7:
        state = "active"
    elif days_since > 21:
        state = "stale"
    else:
        state = "normal"
    return {"last": last_date.isoformat(), "days_since": days_since, "state": state}


def _deadline_from_name(name: str, today: date) -> date | None:
    if not any(word in name for word in ("마감", "까지", "제출")):
        return None
    candidates: list[date] = []
    for month_text, day_text, year_text in _DATE_IN_NAME.findall(name):
        year = int(year_text) if year_text else today.year
        if year < 100:
            year += 2000
        try:
            candidates.append(date(year, int(month_text), int(day_text)))
        except ValueError:
            continue
    future = [item for item in candidates if item >= today]
    return min(future) if future else None


def _project_deadline(payload: Mapping[str, Any], today: date) -> date | None:
    explicit = _date(payload.get("period_end"))
    name = str(payload.get("name_short") or payload.get("name_official") or "")
    named = _deadline_from_name(name, today)
    if named is not None and (explicit is None or named < explicit):
        return named
    return explicit


def _decision(
    decision_id: str,
    kind: str,
    severity: str,
    title: str,
    detail: str,
    evidence: list[str],
    action_hint: str,
    *,
    targets: list[str] | None = None,
    due: str | None = None,
    owner: str | None = None,
    options: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    value = {
        "decision_id": decision_id,
        "kind": kind,
        "severity": severity,
        "title": title,
        "detail": detail,
        "owner": owner,
        "due": due,
        "options": options or [],
        "targets": targets if targets is not None else list(evidence),
        "evidence": list(evidence),
        "action_hint": action_hint,
    }
    validate_decision(value)
    return value


def _build_decisions(
    project_rows: list[dict[str, Any]],
    signal_rows: list[dict[str, Any]],
    ledger_projects: list[Record],
    today: date,
) -> list[dict[str, Any]]:
    decisions: list[dict[str, Any]] = []
    unconfirmed = sorted(
        record.record_id
        for record in ledger_projects
        if not payload_dict(record).get("internal_pm")
    )
    if unconfirmed:
        decisions.append(
            _decision(
                "DEC-ledger-unconfirmed",
                "confirm",
                "check",
                f"신뢰 원장 미확정 {len(unconfirmed)}건",
                f"내부 PM이 비어 있는 과제 {len(unconfirmed)}건은 담당자 확정 전까지 담당 미정으로 표시됩니다.",
                unconfirmed,
                "과제 상세에서 내부 PM 확정",
            )
        )

    deadline_rows: list[tuple[dict[str, Any], date]] = []
    for row in project_rows:
        if row["phase"] == "closed":
            continue
        due = _project_deadline(row["_payload"], today)
        if due is not None and 0 <= (due - today).days <= 30:
            deadline_rows.append((row, due))
    if deadline_rows:
        deadline_rows.sort(key=lambda item: item[1])
        evidence = [f"prj:{row['project_id']}" for row, _ in deadline_rows]
        nearest = deadline_rows[0][1]
        days = (nearest - today).days
        decisions.append(
            _decision(
                "DEC-deadline-near",
                "act",
                "urgent" if days <= 7 else "warn",
                f"마감 임박 {len(deadline_rows)}건",
                f"가장 가까운 마감은 {nearest.isoformat()}이며 {days}일 남았습니다.",
                evidence,
                "제출물과 담당자 확인",
                due=nearest.isoformat(),
            )
        )

    stale = [row for row in project_rows if row["activity"]["state"] == "stale"]
    if stale:
        evidence = [f"prj:{row['project_id']}" for row in stale]
        decisions.append(
            _decision(
                "DEC-activity-stale",
                "act",
                "warn",
                f"활동 정체 {len(stale)}건",
                "최근 파일 활동이 21일을 초과한 과제입니다. 진행 상태를 확인해야 합니다.",
                evidence,
                "과제별 최근 활동과 다음 일정 확인",
            )
        )

    pending_signals = [row for row in signal_rows if row["payload"].get("state") == "확인"]
    if pending_signals:
        evidence = [row["record_id"] for row in pending_signals]
        targets = [
            f"prj:{row['payload']['project_id']}"
            for row in pending_signals
            if row["payload"].get("project_id")
        ]
        decisions.append(
            _decision(
                "DEC-new-signal",
                "act",
                "check",
                f"신규 신호 {len(pending_signals)}건",
                "확인 상태로 남아 있는 외부 요청 또는 신호가 있습니다.",
                evidence,
                "신호 내용을 확인하고 처리 상태 갱신",
                targets=targets,
            )
        )
    return decisions


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8", newline="\n") as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2)
        stream.write("\n")
    temporary.replace(path)


def _write_bundle(path: Path, payload: Any) -> None:
    """Emit the same view models as a JS global so pages work from file:// too."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8", newline="\n") as stream:
        stream.write("window.__RPBD__ = ")
        json.dump(payload, stream, ensure_ascii=False, indent=2)
        stream.write(";\n")
    temporary.replace(path)


def build(
    data_dir: str | Path = "data",
    output_dir: str | Path = "web/data",
    *,
    generated_at: str | None = None,
) -> dict[str, Any]:
    """Build all C6.3-C6.6 JSON files and return the generated meta model."""
    data_path = Path(data_dir)
    output_path = Path(output_dir)
    timestamp = generated_at or iso_now()
    today = _as_of(timestamp)

    store = RecordStore(data_path)
    records = list(store.iter_records())
    merged = merge_records(records)
    merged_rows = list(merged.values())
    project_merged = sorted(
        (row for row in merged_rows if row["record_type"] == "project"),
        key=lambda row: row["payload"].get("project_id", ""),
    )
    document_merged = [row for row in merged_rows if row["record_type"] == "document"]
    signal_merged = [row for row in merged_rows if row["record_type"] == "signal"]

    documents_by_project: dict[str, list[dict[str, Any]]] = defaultdict(list)
    categories_by_project: dict[str, Counter[str]] = defaultdict(Counter)
    for row in document_merged:
        payload = row["payload"]
        project_id = payload.get("project_id")
        if not project_id:
            continue
        grade = payload.get("sensitivity") or classify(
            str(payload.get("name") or ""),
            payload.get("category"),
            str(payload.get("rel_path") or ""),
        )
        item = {
            "record_id": _safe_ref(row["record_id"], grade),
            "name": _display_name(payload, row["record_id"]),
            "category": payload.get("category", "other"),
            "modified": payload.get("modified"),
            "status": row["status"],
        }
        documents_by_project[project_id].append(item)
        if row["status"] == "present":
            categories_by_project[project_id][item["category"]] += 1

    signals_by_project: dict[str, list[dict[str, Any]]] = defaultdict(list)
    signal_rows: list[dict[str, Any]] = []
    for row in signal_merged:
        payload = row["payload"]
        signal_rows.append({"record_id": row["record_id"], "payload": payload})
        project_id = payload.get("project_id")
        if project_id:
            signals_by_project[project_id].append(
                {
                    "signal_id": payload.get("signal_id"),
                    "received": payload.get("received"),
                    "summary": payload.get("summary"),
                    "state": payload.get("state"),
                }
            )

    change_rows = _json_lines((data_path / "changes").glob("*.jsonl")) if (data_path / "changes").exists() else []
    events_by_project: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for event in change_rows:
        project_id = event.get("project_id")
        if project_id:
            events_by_project[project_id].append(event)

    project_rows: list[dict[str, Any]] = []
    for row in project_merged:
        payload = row["payload"]
        project_id = payload.get("project_id")
        if not project_id:
            continue
        period_end = _date(payload.get("period_end"))
        item = {
            "project_id": project_id,
            "name_short": payload.get("name_short") or payload.get("name_official") or project_id,
            "phase": payload.get("phase") or "unknown",
            "period": {
                "start": payload.get("period_start"),
                "end": payload.get("period_end"),
                "days_left": (period_end - today).days if period_end else None,
            },
            "activity": _activity(payload.get("last_activity"), today),
            "counts": {
                "files": len([item for item in documents_by_project[project_id] if item["status"] == "present"]),
                "signals": len(signals_by_project[project_id]),
                "findings": 0,
            },
            "needs_confirmation": list(row.get("needs_confirmation", [])),
            "categories": dict(sorted(categories_by_project[project_id].items())),
            "_payload": payload,
        }
        project_rows.append(item)

    ledger_projects = [record for record in records if record.record_type == "project" and record.provenance == "ledger"]
    decisions = _build_decisions(project_rows, signal_rows, ledger_projects, today)
    for decision in decisions:
        validate_decision(decision)

    findings_by_project: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for decision in decisions:
        for target in decision["targets"]:
            if not target.startswith("prj:"):
                continue
            project_id = target.removeprefix("prj:")
            finding = {
                "finding_id": f"FND-{decision['decision_id'].removeprefix('DEC-')}-{project_id}",
                "severity": decision["severity"],
                "title": decision["title"],
                "action": decision["action_hint"],
                "evidence": decision["evidence"],
            }
            findings_by_project[project_id].append(finding)
    for row in project_rows:
        row["counts"]["findings"] = len(findings_by_project[row["project_id"]])

    manifest = _latest_json(data_path / "manifest")
    drive_records = [record for record in records if record.source.connector == "drive"]
    ledger_records = [record for record in records if record.source.connector == "ledger"]
    drive_scan = max((record.observed_at for record in drive_records), default=None)
    ledger_stamp = max((record.observed_at for record in ledger_records), default=None)
    unavailable = sum(row["status"] == "unavailable" for row in document_merged)
    parse_errors = sum(
        bool(row["payload"].get("parse_error")) for row in document_merged
    )
    rejected = int(manifest.get("counts", {}).get("rejected", len(manifest.get("rejected", []))))
    meta = {
        "generated_at": timestamp,
        "run_id": manifest.get("run_id"),
        "source": {
            "drive_scan": drive_scan,
            "ledger_rev": ledger_stamp[:10] if ledger_stamp else None,
        },
        "counts": {
            "projects": len(project_rows),
            "documents": len(document_merged),
            "signals": len(signal_merged),
            "findings": len(decisions),
        },
        "health": {
            "unavailable_files": unavailable,
            "parse_errors": parse_errors,
            "rejected_records": rejected,
        },
    }

    public_projects = [{key: value for key, value in row.items() if key != "_payload"} for row in project_rows]
    bundle_projects: dict[str, Any] = {}
    _write_json(output_path / "meta.json", meta)
    _write_json(output_path / "decisions.json", {"generated_at": timestamp, "decisions": decisions})
    _write_json(output_path / "projects.json", {"projects": public_projects})

    for row, public in zip(project_rows, public_projects):
        project_id = row["project_id"]
        if not _SAFE_PROJECT_ID.fullmatch(project_id):
            raise ValueError(f"unsafe project_id: {project_id}")
        timeline = []
        for event in sorted(events_by_project[project_id], key=lambda item: item.get("detected_at", ""), reverse=True):
            record = merged.get(event.get("record_id", ""), {})
            payload = record.get("payload", {})
            timeline.append(
                {
                    "detected_at": event.get("detected_at"),
                    "event_type": event.get("event_type"),
                    "title": payload.get("summary") or _display_name(payload, event.get("record_id", "")),
                    "significance": event.get("significance", "normal"),
                }
            )
        detail = {
            "project_id": project_id,
            "profile": {**row["_payload"], **public},
            "documents": sorted(
                documents_by_project[project_id],
                key=lambda item: (item.get("modified") or "", item["name"]),
                reverse=True,
            ),
            "timeline": timeline,
            "signals": signals_by_project[project_id],
            "findings": findings_by_project[project_id],
            "graph": {"nodes": [], "edges": []},
        }
        _write_json(output_path / "project" / f"{project_id}.json", detail)
        bundle_projects[project_id] = detail

    _write_bundle(output_path / "bundle.js", {
        "meta": meta,
        "decisions": {"generated_at": timestamp, "decisions": decisions},
        "projects": {"projects": public_projects},
        "project": bundle_projects,
    })
    return meta


build_viewmodels = build


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", default="data")
    parser.add_argument("--output-dir", default="web/data")
    args = parser.parse_args(argv)
    meta = build(args.data_dir, args.output_dir)
    print(json.dumps(meta["counts"], ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
