"""End-to-end collect -> validate -> store -> changefeed pipeline.

Integration seam between the collector layer (yields C1 record dicts) and the
SSOT layer (operates on Record dataclasses). Owned by the integrator, not by
either producing layer, so neither side has to know about the other's shape.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from rpbd.collect.base import RunContext
from rpbd.collect.drive import DriveCollector
from rpbd.ssot.changefeed import detect_changes
from rpbd.ssot.record import Record
from rpbd.ssot.store import RecordStore

KST = timezone(timedelta(hours=9))
PROJECT_ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = PROJECT_ROOT / "data"


def _now() -> datetime:
    return datetime.now(KST)


def _run_id(started: datetime) -> str:
    return started.strftime("%Y%m%dT%H%M%S")


def _logger() -> logging.Logger:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )
    return logging.getLogger("rpbd.pipeline")


def _load_prev_snapshot(snapshots_dir: Path) -> tuple[dict[str, str], str | None]:
    """Return the most recent snapshot hash map and its name, if any."""
    if not snapshots_dir.exists():
        return {}, None
    files = sorted(snapshots_dir.glob("*.json"))
    if not files:
        return {}, None
    latest = files[-1]
    payload = json.loads(latest.read_text(encoding="utf-8"))
    return payload.get("hashes", {}), latest.name


def _to_records(raw: Iterable[dict[str, Any]], log: logging.Logger) -> tuple[list[Record], list[dict[str, Any]]]:
    """Adapt collector dicts into Record objects. Malformed items are reported, not raised."""
    records: list[Record] = []
    malformed: list[dict[str, Any]] = []
    for item in raw:
        try:
            records.append(Record.from_dict(item))
        except Exception as exc:  # adapter boundary: never abort the whole run
            malformed.append({"record_id": item.get("record_id"), "error": f"{type(exc).__name__}: {exc}"})
            log.warning("adapter rejected record %s: %s", item.get("record_id"), exc)
    return records, malformed


def run(dry_run: bool = False, invoked_by: str = "manual") -> dict[str, Any]:
    log = _logger()
    started = _now()
    run_id = _run_id(started)

    records_dir = DATA_DIR / "records"
    snapshots_dir = DATA_DIR / "snapshots"
    changes_dir = DATA_DIR / "changes"
    manifest_dir = DATA_DIR / "manifest"
    for d in (records_dir, snapshots_dir, changes_dir, manifest_dir):
        d.mkdir(parents=True, exist_ok=True)

    prev_hashes, prev_name = _load_prev_snapshot(snapshots_dir)
    log.info("previous snapshot: %s (%d records)", prev_name or "none", len(prev_hashes))

    ctx = RunContext(
        run_id=run_id,
        started_at=started,
        logger=log,
        dry_run=dry_run,
        prev_snapshot=prev_hashes,
    )

    collector = DriveCollector()
    raw = list(collector.collect(ctx))
    log.info("collected %d raw records", len(raw))

    records, malformed = _to_records(raw, log)

    store = RecordStore(DATA_DIR)
    prev_current = store.current()
    result = store.append(records) if not dry_run else None

    accepted = result.accepted if result else records
    rejected = [r.to_dict() for r in result.rejected] if result else []
    log.info("accepted %d / rejected %d / malformed %d", len(accepted), len(rejected), len(malformed))

    events = list(
        detect_changes(prev_current, {r.record_id: r for r in accepted}, started.isoformat())
    )
    if not dry_run and events:
        feed = changes_dir / f"{started:%Y-%m-%d}.jsonl"
        with feed.open("a", encoding="utf-8") as fh:
            for event in events:
                fh.write(json.dumps(event.to_dict() if hasattr(event, "to_dict") else event.__dict__,
                                    ensure_ascii=False, default=str) + "\n")

    snapshot = {
        "run_id": run_id,
        "generated_at": started.isoformat(),
        "hashes": {r.record_id: r.content_hash for r in accepted},
    }
    if not dry_run:
        (snapshots_dir / f"{started:%Y-%m-%dT%H%M}.json").write_text(
            json.dumps(snapshot, ensure_ascii=False), encoding="utf-8"
        )

    by_significance: dict[str, int] = {}
    by_type: dict[str, int] = {}
    for event in events:
        sig = getattr(event, "significance", "normal")
        by_significance[sig] = by_significance.get(sig, 0) + 1
        et = getattr(event, "event_type", "unknown")
        by_type[et] = by_type.get(et, 0) + 1

    provenance_counts: dict[str, int] = {}
    status_counts: dict[str, int] = {}
    for r in accepted:
        provenance_counts[r.provenance] = provenance_counts.get(r.provenance, 0) + 1
        status_counts[r.status] = status_counts.get(r.status, 0) + 1

    manifest = {
        "run_id": run_id,
        "started_at": started.isoformat(),
        "finished_at": _now().isoformat(),
        "invoked_by": invoked_by,
        "dry_run": dry_run,
        "prev_snapshot": prev_name,
        "counts": {
            "collected": len(raw),
            "accepted": len(accepted),
            "rejected": len(rejected),
            "malformed": len(malformed),
            "events": len(events),
        },
        "provenance": provenance_counts,
        "status": status_counts,
        "events_by_type": by_type,
        "events_by_significance": by_significance,
        "rejected": rejected[:50],
        "malformed": malformed[:50],
    }
    if not dry_run:
        (manifest_dir / f"{started:%Y-%m-%d}.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    return manifest


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the RPBD collect->store pipeline once.")
    parser.add_argument("--dry-run", action="store_true", help="collect and report without writing")
    parser.add_argument("--invoked-by", default="manual", choices=["manual", "watcher", "cron"])
    args = parser.parse_args()

    manifest = run(dry_run=args.dry_run, invoked_by=args.invoked_by)
    print(json.dumps(manifest, ensure_ascii=False, indent=2)[:2000])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
