"""Collect documents that staff save from Claude Project or Cowork.

Submissions land in <root>/00_raw/<person>/<YYYY-MM>/ and become document
records. Attribution to a project happens in rpbd.ingest.attribute, keeping
collection (what exists) separate from interpretation (what it belongs to).
"""

from __future__ import annotations

from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any, Iterable, Iterator

from rpbd.collect.base import RunContext, compute_metadata_hash, get_iso_now
from rpbd.ingest.attribute import ProjectAlias, attribute
from rpbd.ssot.sensitivity import classify, may_extract_body

KST = timezone(timedelta(hours=9))

# Bodies are read only for text formats; the rest contribute metadata alone.
TEXT_EXTS = {".md", ".txt"}
SUPPORTED_EXTS = TEXT_EXTS | {".docx", ".xlsx", ".pdf", ".pptx", ".hwp", ".hwpx"}


class SubmissionCollector:
    """Emit document records for staff submissions."""

    name = "submission"

    def __init__(self, root: Path | str, aliases: Iterable[ProjectAlias] = ()) -> None:
        self.root = Path(root)
        self.aliases = list(aliases)

    def collect(self, ctx: RunContext) -> Iterator[dict[str, Any]]:
        raw_dir = self.root / "00_raw"
        if not raw_dir.is_dir():
            if ctx and ctx.logger:
                ctx.logger.warning("submission root missing: %s", raw_dir)
            return

        observed_at = get_iso_now()
        for path in sorted(raw_dir.rglob("*")):
            if not path.is_file() or path.name.startswith("."):
                continue
            if path.suffix.lower() not in SUPPORTED_EXTS:
                continue
            yield self._record(path, raw_dir, observed_at, ctx)

    def _record(
        self, path: Path, raw_dir: Path, observed_at: str, ctx: RunContext
    ) -> dict[str, Any]:
        rel = path.relative_to(raw_dir)
        parts = rel.parts
        author = parts[0] if len(parts) > 1 else None

        sensitivity = classify(path.name, "submission")
        text: str | None = None
        parse_error: str | None = None
        if path.suffix.lower() in TEXT_EXTS and may_extract_body(sensitivity):
            try:
                text = path.read_text(encoding="utf-8")
            except Exception as err:
                parse_error = f"{type(err).__name__}: {err}"
                if ctx and ctx.logger:
                    ctx.logger.warning("submission unreadable (%s): %s", path.name, err)
        elif not may_extract_body(sensitivity):
            parse_error = "blocked: sensitive"

        result = attribute(filename=path.name, text=text, aliases=self.aliases)

        try:
            stat = path.stat()
            size, modified = stat.st_size, datetime.fromtimestamp(stat.st_mtime, KST).date().isoformat()
            status = "present"
        except OSError as err:
            size, modified = 0, ""
            status = "unavailable"
            parse_error = parse_error or f"{type(err).__name__}: {err}"

        payload = {
            "project_id": result.project_id,
            "name": path.name,
            "rel_path": str(rel),
            "ext": path.suffix.lstrip("."),
            "size_bytes": size,
            "modified": modified,
            "category": "submission",
            "category_label": "담당자 제출물",
            "stage": author or "",
            "author": author,
            "text_extracted": bool(text) and parse_error is None,
            "text_path": None,
            "parse_error": parse_error,
            "attribution_method": result.method,
            "attribution_matched_on": result.matched_on,
            "needs_confirmation": result.needs_confirmation,
        }
        return {
            "record_id": f"sub:{rel}".replace(":", "%3A", 1).replace("sub%3A", "sub:", 1),
            "record_type": "document",
            "schema_version": 1,
            "content_hash": compute_metadata_hash(str(rel), size, modified),
            "hash_basis": "metadata",
            "observed_at": observed_at,
            "source": {"connector": "manual", "root": "submissions", "path": str(rel)},
            "provenance": result.provenance if result.provenance != "unresolved" else "inferred",
            "status": status,
            "sensitivity": sensitivity,
            "payload": payload,
        }


__all__ = ["SubmissionCollector", "SUPPORTED_EXTS", "TEXT_EXTS"]
