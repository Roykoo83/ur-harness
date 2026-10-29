#!/usr/bin/env python3
"""SSOT file index over the Drive API: `_시스템/색인/files.tsv` + `manifest.json`.

Members' AI agents are told to `grep files.tsv` before looking for a file, so a stale
index makes everyone miss files that exist. This builds it from the **API listing**,
not from the local mount (a mount can lag hundreds of files behind, 2026-10-01).

Outputs (same format as the original mount-based indexer):
  _시스템/색인/files.tsv      파일명 · 폴더 · 확장자 · bytes · 수정일  (one row per file, NFC)
  _시스템/색인/manifest.json  per-folder counts and subfolders
  <folder>/_INDEX.md         frontmatter counts refreshed (depth <= 2), human body kept

Guard: if the file count drops sharply against the previous index, nothing is written
and `_시스템/상태/색인_보류.json` explains why. `--force` overrides after a human check.

Run:  python3 core/ssot_index.py [--force] [--dry]
"""
from __future__ import annotations

import argparse
import csv
import datetime
import json
import re
import subprocess
import sys
import tempfile
import unicodedata
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from lib import hb                                            # noqa: E402
from lib import ssot_io                                       # noqa: E402

SERVICE = "ssot-index"
SKIP_DIRS = {".claude", ".tmp.driveupload", ".tmp.drivedownload"}
INDEX_NAME = "_INDEX.md"
IDX_DIR = "_시스템/색인"
HOLD_NOTE = "_시스템/상태/색인_보류.json"
GUARD_DROP_PCT = 5.0       # stop if the file count falls this much vs the previous index
GUARD_DROP_MIN = 50        # ...and by at least this many files
STUB_DEPTH = 2             # _INDEX.md frontmatter is kept for top two levels only
FRONT_RE = re.compile(r"^---\n(.*?)\n---\n", re.DOTALL)
COUNT_RE = re.compile(r"^(files_direct|files_subtree):\s*(\d+)\s*$", re.M)


def nfc(s: str) -> str:
    return unicodedata.normalize("NFC", s)


def now_iso() -> str:
    return datetime.datetime.now().astimezone().strftime("%Y-%m-%dT%H:%M:%S%z")


def _rclone() -> str:
    exe = ssot_io.RCLONE or ssot_io._rclone()                 # noqa: SLF001
    if not exe:
        raise RuntimeError("rclone 없음 — 색인은 API 로만 만든다")
    return exe


def skipped(parts: list[str]) -> bool:
    return any(p.startswith(".") or p in SKIP_DIRS for p in parts)


def list_api() -> list[dict]:
    """Every object in the SSOT drive (files and folders), via the read-only remote."""
    r = subprocess.run([_rclone(), "lsjson", "-R", "--fast-list", "--no-mimetype",
                        ssot_io.RO], capture_output=True, text=True, timeout=1800)
    if r.returncode != 0:
        raise RuntimeError(f"목록 조회 실패 rc={r.returncode}: {r.stderr.strip()[-200:]}")
    return json.loads(r.stdout or "[]")


def build(items: list[dict]) -> tuple[dict, list[list]]:
    """(manifest folders, files.tsv rows) in the original indexer's format."""
    dirs: set[str] = {"."}
    rows: list[list] = []
    direct: dict[str, int] = {}
    for it in items:
        path = nfc(it["Path"])
        parts = path.split("/")
        if it.get("IsDir"):
            if not skipped(parts):
                dirs.add(path)
            continue
        name, folder = parts[-1], "/".join(parts[:-1]) or "."
        if skipped(parts[:-1]) or name.startswith(".") or name == INDEX_NAME:
            continue
        mod = (it.get("ModTime") or "")[:10] or "unavailable"
        rows.append([name, folder, Path(name).suffix.lstrip(".").lower(),
                     it.get("Size", -1), mod])
        direct[folder] = direct.get(folder, 0) + 1
    subtree = dict(direct)
    for folder, n in direct.items():                          # roll counts up to every ancestor
        parts = folder.split("/") if folder != "." else []
        for i in range(len(parts)):
            anc = "/".join(parts[:i]) or "."
            subtree[anc] = subtree.get(anc, 0) + n
    children: dict[str, list[str]] = {d: [] for d in dirs}
    for d in dirs:
        if d == ".":
            continue
        parent = d.rsplit("/", 1)[0] if "/" in d else "."
        children.setdefault(parent, []).append(d.rsplit("/", 1)[-1])
    folders = {d: {"files_direct": direct.get(d, 0),
                   "files_subtree": subtree.get(d, 0),
                   "subfolders": sorted(children.get(d, []))}
               for d in sorted(dirs)}
    rows.sort(key=lambda r: (r[1], r[0]))
    return folders, rows


def previous_rows(tmp: Path) -> int | None:
    p = tmp / "prev_files.tsv"
    if not ssot_io.get(f"{IDX_DIR}/files.tsv", p):
        return None
    with p.open(encoding="utf-8") as fh:
        return max(sum(1 for _ in fh) - 1, 0)


def guard_reason(rows: int, prev: int | None) -> str | None:
    if not prev:
        return None
    drop = prev - rows
    if drop >= GUARD_DROP_MIN and drop / prev * 100 >= GUARD_DROP_PCT:
        return (f"드라이브 파일이 직전 색인보다 {drop}건 적다 ({prev}→{rows}, "
                f"-{drop / prev * 100:.1f}%). 대량 삭제·이동일 수 있다 — 사람이 확인해야 한다")
    return None


def write(tmp: Path, name: str, rel: str, text: str) -> None:
    f = tmp / name
    f.write_text(text, encoding="utf-8")
    ok, msg = ssot_io.put(f, rel)
    if not ok:
        raise RuntimeError(f"{rel} 올리기 실패: {msg}")


def refresh_index_md(tmp: Path, folders: dict) -> int:
    """Refresh counts in each top-level `_INDEX.md`, keeping the human-written body.

    Writes only when a count changed or the file is missing, so a quiet drive is not
    rewritten every three hours.
    """
    changed = 0
    for rel, info in folders.items():
        parts = rel.split("/")
        if rel == "." or parts[0] == "_시스템" or len(parts) > STUB_DEPTH:
            continue
        local = tmp / "idx" / rel / INDEX_NAME
        body, old = None, {}
        if ssot_io.get(f"{rel}/{INDEX_NAME}", local):
            text = local.read_text(encoding="utf-8")
            m = FRONT_RE.match(text)
            old = dict(COUNT_RE.findall(m.group(1))) if m else {}
            body = text[m.end():] if m else text
        new = {"files_direct": str(info["files_direct"]),
               "files_subtree": str(info["files_subtree"])}
        if body is not None and old == new:
            continue
        if body is None:
            body = ("\n## 이 폴더는\n(설명 미기입: 담당자·에이전트가 채울 것)\n\n"
                    "## 여기 없는 것\n| 찾는 것 | 실제 위치 |\n|---|---|\n| (미기입) | |\n")
        fm = (f'---\npath: "{rel}"\nfiles_direct: {new["files_direct"]}\n'
              f'files_subtree: {new["files_subtree"]}\nsensitivity_max: "internal"\n'
              f'generated: {now_iso()}\nstale_after: "P7D"\n---\n')
        local.parent.mkdir(parents=True, exist_ok=True)
        local.write_text(fm + body, encoding="utf-8")
        ok, msg = ssot_io.put(local, f"{rel}/{INDEX_NAME}")
        if ok:
            changed += 1
        else:
            print(f"  ! {rel}/{INDEX_NAME}: {msg}", file=sys.stderr)
    return changed


def clear_hold_note() -> None:
    """Remove a previous guard note once the index is healthy again."""
    remote = ssot_io._remotes()[0]                            # noqa: SLF001
    subprocess.run([_rclone(), "deletefile", f"{remote}{HOLD_NOTE}"],
                   capture_output=True, text=True, timeout=120)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--force", action="store_true", help="guard off, after a human check")
    ap.add_argument("--dry", action="store_true", help="build and report, write nothing")
    a = ap.parse_args()
    try:
        folders, rows = build(list_api())
    except Exception as e:                                    # noqa: BLE001
        hb.fail(SERVICE, str(e))
        print(f"FAIL {e}", file=sys.stderr)
        return 1
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        prev = previous_rows(tmp)
        reason = None if a.force else guard_reason(len(rows), prev)
        print(f"drive files={len(rows)} folders={len(folders)} previous={prev}")
        if a.dry:
            print(f"DRY guard={'BLOCK: ' + reason if reason else 'pass'}")
            return 0
        if reason:
            write(tmp, "hold.json", HOLD_NOTE, json.dumps(
                {"generated": now_iso(), "status": "blocked", "reason": reason,
                 "api_files": len(rows), "previous_files": prev,
                 "effect": "files.tsv·manifest.json 을 갱신하지 않았다. 직전 색인이 그대로 남아 있다"},
                ensure_ascii=False, indent=1))
            hb.blocked(SERVICE, reason, needs="확인 후 ssot_index.py --force")
            print(f"BLOCKED {reason}", file=sys.stderr)
            return 2
        try:
            buf = tmp / "files.tsv"
            with buf.open("w", encoding="utf-8", newline="") as fh:
                w = csv.writer(fh, delimiter="\t")
                w.writerow(["파일명", "폴더", "확장자", "bytes", "수정일"])
                w.writerows(rows)
            ok, msg = ssot_io.put(buf, f"{IDX_DIR}/files.tsv")
            if not ok:
                raise RuntimeError(f"files.tsv 올리기 실패: {msg}")
            write(tmp, "manifest.json", f"{IDX_DIR}/manifest.json", json.dumps(
                {"generated": now_iso(), "root": "SSOT", "source": "drive-api",
                 "folders": folders}, ensure_ascii=False, indent=1))
            clear_hold_note()
            stubs = refresh_index_md(tmp, folders)
        except Exception as e:                                # noqa: BLE001
            hb.fail(SERVICE, str(e))
            print(f"FAIL {e}", file=sys.stderr)
            return 1
    hb.ok(SERVICE, outputs=[f"{IDX_DIR}/files.tsv", f"{IDX_DIR}/manifest.json"],
          counters={"files": len(rows), "folders": len(folders), "index_md_updated": stubs})
    print(f"OK files={len(rows)} folders={len(folders)} _INDEX.md 갱신={stubs} -> {IDX_DIR}/")
    return 0


if __name__ == "__main__":
    sys.exit(main())
