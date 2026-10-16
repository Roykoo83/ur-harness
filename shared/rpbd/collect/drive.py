"""Drive collector for R&D active/applying project folders and ledger archives.
"""

import csv
import hashlib
import logging
import os
import re
import unicodedata
from pathlib import Path
from typing import Any, Iterator, Optional

from rpbd.ssot.sensitivity import classify
from rpbd.collect.base import Collector, RunContext, make_record, get_iso_now


def nfc(s: str) -> str:
    """Normalize string to NFC format."""
    return unicodedata.normalize("NFC", s) if s else ""


def escape_rel_path(path: str) -> str:
    """Escape colon characters in relative path according to C1.4 spec."""
    return path.replace(":", "%3A")


CATEGORIES = [
    ("budget_exec", "예산집행/증빙", ["증빙", "지출결의", "급여", "인건비", "이체증", "영수증", "세금계산서", "카드", "정산", "집행"]),
    ("budget_reg",  "예산등록/관리", ["예산", "사업비", "산출내역", "rcms", "사업비관리", "과제등록", "선금", "연구비"]),
    ("contract",    "협약/계약",    ["협약", "계약", "약정"]),
    ("admin",       "행정/제출",    ["제출서류", "신청서", "동의서", "확인서", "증명", "서명", "인감", "접수", "보완서류", "제출"]),
    ("perform",     "수행/성과",    ["수행단계", "wbs", "회의", "pmo", "진도", "성과", "어노테이션", "보고서", "월간", "중간"]),
    ("plan",        "기획/제안",    ["계획서", "제안", "사업계획", "공고", "안내서", "발표", "rfp"]),
]


def classify(path_lower: str, name_lower: str) -> tuple[str, str]:
    """Classify document category using folder path and file name keywords."""
    for key, label, kws in CATEGORIES:
        for kw in kws:
            if kw in path_lower or kw in name_lower:
                return key, label
    return "other", "기타/참고"


DEFAULT_DRIVE_ACTIVE = "{{MOUNT_ROOT}}/공유 드라이브/U/2. R&D 사업/2-3. 수행중인 사업"
DEFAULT_ARCHIVE = "{{MOUNT_ROOT}}/공유 드라이브/U/{{WORKSPACE_NAME}}_Workflow/연구기획_프로젝트_아카이브"

TOP_LEVEL_WHITELIST = (
    "1. 연구기획사업부",
    "2. R&D 사업",
    "3. 인허가 품질관리",
    "4. R&D 예산_통합",
    "5. Naverworks",
)

TOP_LEVEL_COLLECTION_ENABLED = {
    "1. 연구기획사업부": False,
    "2. R&D 사업": True,
    "3. 인허가 품질관리": False,
    "4. R&D 예산_통합": False,
    "5. Naverworks": False,
}

APPLYING_MINISTRY_ALIASES = (
    ("\uc911\uc18c\ubca4\ucc98\uae30\uc5c5\ubd80", "\uc911\uc18c\ubca4\ucc98\uae30\uc5c5\ubd80"),
    ("\uc911\uae30\ubd80", "\uc911\uc18c\ubca4\ucc98\uae30\uc5c5\ubd80"),
    ("\ubcf4\uac74\ubcf5\uc9c0\ubd80", "\ubcf4\uac74\ubcf5\uc9c0\ubd80"),
    ("\ubcf5\uc9c0\ubd80", "\ubcf4\uac74\ubcf5\uc9c0\ubd80"),
    ("\uacfc\ud559\uae30\uc220\uc815\ubcf4\ud1b5\uc2e0\ubd80", "\uacfc\ud559\uae30\uc220\uc815\ubcf4\ud1b5\uc2e0\ubd80"),
    ("\uacfc\uae30\uc815\ud1b5\ubd80", "\uacfc\ud559\uae30\uc220\uc815\ubcf4\ud1b5\uc2e0\ubd80"),
)


def infer_applying_metadata(folder_name: str) -> tuple[Optional[str], Optional[str]]:
    """Infer applying-project period end and ministry only from explicit folder text."""
    normalized = nfc(folder_name)
    ministry = next(
        (canonical for alias, canonical in APPLYING_MINISTRY_ALIASES if alias in normalized),
        None,
    )

    period_match = re.search(
        r"\b\d{4}[.-]\d{1,2}(?:[.-]\d{1,2})?\s*[-~]\s*"
        r"(\d{4})[.-](\d{1,2})(?:[.-](\d{1,2}))?\b",
        normalized,
    )
    if not period_match:
        return None, ministry

    year, month, day = period_match.groups()
    period_end = f"{year}-{int(month):02d}"
    if day:
        period_end += f"-{int(day):02d}"
    return period_end, ministry


class DriveCollector:
    """Collector for Google Drive project directories and ledger CSV archives."""
    name: str = "drive"

    def __init__(
        self,
        active_dir: str | Path | None = None,
        archive_dir: str | Path | None = None,
    ):
        self.active_dir = Path(active_dir) if active_dir else Path(DEFAULT_DRIVE_ACTIVE)
        self.archive_dir = Path(archive_dir) if archive_dir else Path(DEFAULT_ARCHIVE)
        self.drive_root = self.active_dir.parent.parent

    def collect(self, ctx: RunContext) -> Iterator[dict[str, Any]]:
        """Collect C1 compliant records from ledger archives and Drive folders."""
        obs_time = ctx.started_at.isoformat() if ctx and ctx.started_at else get_iso_now()

        # 1. Source B: Ledger Archive (reference/project_master.csv)
        yield from self._collect_master_projects(ctx, obs_time)

        # 2. Source B: Ledger Archive (signals/signals.csv)
        yield from self._collect_signals(ctx, obs_time)

        # 3. Source A: Active & Applying Drive Folders
        yield from self._collect_drive_folders(ctx, obs_time)

    def _collect_master_projects(self, ctx: RunContext, obs_time: str) -> Iterator[dict[str, Any]]:
        """Collect project records from reference/project_master.csv (provenance=ledger)."""
        master_csv = self.archive_dir / "reference" / "project_master.csv"
        if not master_csv.exists():
            if ctx and ctx.logger:
                ctx.logger.warning(f"Ledger file not found: {master_csv}")
            return

        try:
            with open(master_csv, encoding="utf-8-sig") as f:
                reader = csv.DictReader(f)
                for row in reader:
                    row = {nfc(k): nfc(v).strip() for k, v in row.items() if k}
                    pid = row.get("project_id", "")
                    if not pid:
                        continue

                    phase_raw = row.get("현재단계", "")
                    if "수행" in phase_raw:
                        phase = "active"
                    elif "정산" in phase_raw or "종료" in phase_raw:
                        phase = "closed"
                    elif "지원" in phase_raw:
                        phase = "applying"
                    else:
                        phase = "active"

                    payload = {
                        "project_id": pid,
                        "name_official": row.get("정식명") or None,
                        "name_short": row.get("약칭") or None,
                        "phase": phase,
                        "period_start": row.get("협약시작") or None,
                        "period_end": row.get("협약종료") or None,
                        "ministry": row.get("부처") or None,
                        "agency": row.get("전담기관") or None,
                        "folder_path": None,
                        "file_count": None,
                        "total_bytes": None,
                        "last_activity": None,
                    }

                    yield make_record(
                        record_id=f"prj:{pid}",
                        record_type="project",
                        payload=payload,
                        source={
                            "connector": "ledger",
                            "root": "archive",
                            "path": "reference/project_master.csv",
                        },
                        provenance="ledger",
                        status="present",
                        hash_basis="fields",
                        observed_at=obs_time,
                    )
        except Exception as err:
            if ctx and ctx.logger:
                ctx.logger.error(f"Failed reading ledger project_master.csv: {err}")

    def _collect_signals(self, ctx: RunContext, obs_time: str) -> Iterator[dict[str, Any]]:
        """Collect signal records from signals/signals.csv (provenance=ledger)."""
        signals_csv = self.archive_dir / "signals" / "signals.csv"
        if not signals_csv.exists():
            if ctx and ctx.logger:
                ctx.logger.warning(f"Signals file not found: {signals_csv}")
            return

        try:
            with open(signals_csv, encoding="utf-8-sig") as f:
                reader = csv.DictReader(f)
                for row in reader:
                    row = {nfc(k): nfc(v).strip() for k, v in row.items() if k}
                    sig_id = row.get("signal_id", "")
                    if not sig_id:
                        continue

                    payload = {
                        "signal_id": sig_id,
                        "received": row.get("수신일") or None,
                        "from_org": row.get("출처") or None,
                        "project_id": row.get("관련_project_id") or None,
                        "kind": row.get("유형") or None,
                        "summary": row.get("요약") or None,
                        "link": row.get("원문링크") or None,
                        "due": row.get("마감일") or None,
                        "state": row.get("처리상태") or None,
                    }

                    yield make_record(
                        record_id=f"sig:{sig_id}",
                        record_type="signal",
                        payload=payload,
                        source={
                            "connector": "ledger",
                            "root": "archive",
                            "path": "signals/signals.csv",
                        },
                        provenance="ledger",
                        status="present",
                        hash_basis="fields",
                        observed_at=obs_time,
                    )
        except Exception as err:
            if ctx and ctx.logger:
                ctx.logger.error(f"Failed reading signals.csv: {err}")

    def _collect_drive_folders(self, ctx: RunContext, obs_time: str) -> Iterator[dict[str, Any]]:
        """Scan active and applying project folders in Google Drive."""
        availability_records, available_folders = self._top_level_availability(ctx, obs_time)
        yield from availability_records

        rd_folder = "2. R&D 사업"
        if not TOP_LEVEL_COLLECTION_ENABLED[rd_folder] or rd_folder not in available_folders:
            return

        # 1) Active Projects (2-3. 수행중인 사업)
        if self.active_dir.exists() and self.active_dir.is_dir():
            yield from self._scan_phase_dir(self.active_dir, "active", "active", ctx, obs_time)

        # 2) Applying Projects (2-2. 지원중인 사업 - sibling folder)
        applying_dir = self.active_dir.parent / "2-2. 지원중인 사업"
        if applying_dir.exists() and applying_dir.is_dir():
            yield from self._scan_phase_dir(applying_dir, "applying", "applying", ctx, obs_time)

    def _top_level_availability(
        self,
        ctx: RunContext,
        obs_time: str,
    ) -> tuple[list[dict[str, Any]], set[str]]:
        """Return stable availability records for every expected U top-level folder."""
        try:
            listed_entries = {
                nfc(entry): entry
                for entry in os.listdir(self.drive_root)
            }
            list_error = None
        except Exception as err:
            listed_entries = {}
            list_error = err

        records: list[dict[str, Any]] = []
        available: set[str] = set()
        for folder_name in TOP_LEVEL_WHITELIST:
            raw_name = listed_entries.get(folder_name)
            folder_path = self.drive_root / raw_name if raw_name else None
            folder_error = None
            try:
                is_available = bool(folder_path and folder_path.is_dir())
            except Exception as err:
                is_available = False
                folder_error = err
            if is_available:
                available.add(folder_name)
                parse_error = None
            else:
                if folder_error is not None:
                    parse_error = f"{type(folder_error).__name__}: {folder_error}"
                elif list_error is not None:
                    parse_error = f"{type(list_error).__name__}: {list_error}"
                else:
                    parse_error = "Expected top-level folder missing from Drive listing"
                if ctx and ctx.logger:
                    ctx.logger.warning(
                        "Expected Drive top-level folder unavailable: %s (%s)",
                        folder_name,
                        parse_error,
                    )

            payload = {
                "project_id": None,
                "name": folder_name,
                "rel_path": folder_name,
                "ext": "",
                "size_bytes": 0,
                "modified": None,
                "category": "other",
                "category_label": "기타/참고",
                "stage": None,
                "text_extracted": False,
                "text_path": None,
                "parse_error": parse_error,
            }
            records.append(
                make_record(
                    record_id=f"doc:DRIVE-U:.folder-status/{escape_rel_path(folder_name)}",
                    record_type="document",
                    payload=payload,
                    source={
                        "connector": "drive",
                        "root": "U",
                        "path": folder_name,
                    },
                    provenance="observed",
                    status="present" if is_available else "unavailable",
                    hash_basis="fields",
                    observed_at=obs_time,
                )
            )

        return records, available

    def _scan_phase_dir(
        self,
        base_dir: Path,
        phase: str,
        root_label: str,
        ctx: RunContext,
        obs_time: str,
    ) -> Iterator[dict[str, Any]]:
        """Scan directories under base_dir for project folders and files."""
        try:
            entries = sorted(os.listdir(base_dir))
        except Exception as err:
            if ctx and ctx.logger:
                ctx.logger.error(f"Failed to list directory {base_dir}: {err}")
            return

        for entry in entries:
            pdir = base_dir / entry
            if not pdir.is_dir():
                continue

            entry_nfc = nfc(entry)
            if phase == "applying" and entry_nfc.startswith("_"):
                continue

            match_num = re.match(r"\s*(\d+)\.", entry_nfc)
            if match_num:
                pid = f"PRJ-N{int(match_num.group(1)):03d}"
            else:
                pid = "PRJ-" + hashlib.md5(entry_nfc.encode("utf-8")).hexdigest()[:6].upper()

            file_count = 0
            total_bytes = 0
            last_activity = None

            # Walk through subdirectories and files
            for root, dirs, fnames in os.walk(pdir):
                dirs[:] = [d for d in dirs if not d.startswith(".")]
                for fn in fnames:
                    if fn.startswith(".") or fn.startswith("~$"):
                        continue

                    fp = Path(root) / fn
                    fn_nfc = nfc(fn)
                    try:
                        rel_path_nfc = nfc(os.path.relpath(fp, pdir))
                    except Exception:
                        rel_path_nfc = fn_nfc

                    path_for_classify = nfc(str(fp))
                    rel_parts = rel_path_nfc.split(os.sep)
                    stage = rel_parts[0] if len(rel_parts) > 1 else None

                    is_unavailable = False
                    parse_err_msg = None
                    size_bytes = 0
                    mtime_str = None

                    # Attempt file stat
                    try:
                        st = os.stat(fp)
                        size_bytes = st.st_size
                        mtime_str = os.datetime.fromtimestamp(st.st_mtime).strftime("%Y-%m-%d") if hasattr(os, 'datetime') else None
                    except OSError as err:
                        # DriveFS streaming / OS error failure handling - DO NOT CRASH
                        is_unavailable = True
                        parse_err_msg = f"OSError [Errno {err.errno}]: {err.strerror}"
                        if ctx and ctx.logger:
                            ctx.logger.warning(f"Drive file unavailable ({fp}): {err}")
                    except Exception as err:
                        is_unavailable = True
                        parse_err_msg = f"{type(err).__name__}: {err}"
                        if ctx and ctx.logger:
                            ctx.logger.warning(f"File stat error ({fp}): {err}")

                    # Fix mtime formatting using standard datetime if needed
                    if mtime_str is None and not is_unavailable:
                        try:
                            st = os.stat(fp)
                            from datetime import datetime
                            mtime_str = datetime.fromtimestamp(st.st_mtime).strftime("%Y-%m-%d")
                        except Exception:
                            pass

                    ck, cl = classify(path_for_classify.lower(), fn_nfc.lower())
                    ext = os.path.splitext(fn_nfc)[1].lower().lstrip(".")

                    doc_payload = {
                        "project_id": pid,
                        "name": fn_nfc,
                        "rel_path": rel_path_nfc,
                        "ext": ext,
                        "size_bytes": size_bytes,
                        "modified": mtime_str,
                        "category": ck,
                        "category_label": cl,
                        "stage": stage,
                        "text_extracted": False,
                        "text_path": None,
                        "parse_error": parse_err_msg,
                    }

                    status_val = "unavailable" if is_unavailable else "present"
                    escaped_rel = escape_rel_path(rel_path_nfc)
                    doc_id = f"doc:{pid}:{escaped_rel}"

                    try:
                        rel_to_base_parent = nfc(os.path.relpath(fp, base_dir.parent))
                    except Exception:
                        rel_to_base_parent = rel_path_nfc

                    doc_source = {
                        "connector": "drive",
                        "root": root_label,
                        "path": rel_to_base_parent,
                    }

                    doc_rec = make_record(
                        record_id=doc_id,
                        record_type="document",
                        payload=doc_payload,
                        source=doc_source,
                        provenance="observed",
                        status=status_val,
                        hash_basis="metadata" if not is_unavailable else "fields",
                        observed_at=obs_time,
                    )

                    yield doc_rec

                    if not is_unavailable:
                        file_count += 1
                        total_bytes += size_bytes
                        if mtime_str and (last_activity is None or mtime_str > last_activity):
                            last_activity = mtime_str

            # Inferred project record from Drive folder scan
            period_end = None
            ministry = None
            if phase == "applying":
                period_end, ministry = infer_applying_metadata(entry_nfc)

            proj_payload = {
                "project_id": pid,
                "name_official": entry_nfc,
                "name_short": entry_nfc,
                "phase": phase,
                "period_start": None,
                "period_end": period_end,
                "ministry": ministry,
                "agency": None,
                "folder_path": entry_nfc,
                "file_count": file_count,
                "total_bytes": total_bytes,
                "last_activity": last_activity,
            }

            proj_rec = make_record(
                record_id=f"prj:{pid}",
                record_type="project",
                payload=proj_payload,
                source={
                    "connector": "drive",
                    "root": root_label,
                    "path": entry_nfc,
                },
                provenance="inferred",
                status="present",
                hash_basis="fields",
                observed_at=obs_time,
            )

            yield proj_rec
