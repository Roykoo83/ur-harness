#!/usr/bin/env python3
"""50_대시보드 — 자동화가 만든 파일의 **옛 세대만** 걷어낸다. 사람 파일은 절대 건드리지 않는다.

2026-09-08 사고: publish_dashboards 의 rclone sync 가 스테이지에 없는 파일을 전부 지워
{{MEMBER_B}} 리더의 KSH/ 4건 등 사람 파일 7건을 휴지통으로 보냈다. untrash 로 복구했지만 그때
휴지통에 있던 옛 기계 세대 140여 건도 함께 돌아왔다. 이 도구는 그 세대만 정리한다.

판정 규칙 (보수적):
  - **기계 파일 = 스테이지(~/{{WORKSPACE_NAME}}/state/dash_stage)에 같은 상대경로가 있는 이름**만.
    스테이지에 없는 이름(KSH/*, _ksh.html, 결정판_상태.js, _기획_*.md …)은 목록에도 올리지 않는다.
  - 기계 이름의 사본들(dedupe rename 이 붙인 `-1` `-2`, DriveFS 의 ` 2` ` (1)` ` 2 (1)`) 가운데
      · 해시가 스테이지와 같다  → 정본 후보. 하나만 정식 이름으로 남긴다
      · 크기가 스테이지의 ±25% 안 → 옛 기계 세대 → 삭제(휴지통)
      · 그 밖(크기가 크게 다름)   → **사람 손이 닿았을 수 있다** → 삭제 안 함, `<이름>_<시각>_미확인.<ext>` 로 이름만 바꿔 보존
  - `KSH/` 등 하위 폴더, `_` 접두 파일은 통째로 제외.

사용: dash_generations_cleanup.py [--apply]   (기본 dry-run)
"""
from __future__ import annotations
import argparse, hashlib, json, os, re, subprocess, unicodedata
from pathlib import Path
from lib.config import paths

STAGE = paths.state / "dash_stage"
BASE = "{{DEPT_ROOT}}/50_대시보드"
BOARDS = ["10_운영OS", "20_정부과제시스템"]
RO, RW = "SSOT_ro:", "SSOT_rw:"
# 사본 접미 — dedupe rename 의 -N, DriveFS 의 ' N', ' (N)', ' N (M)'
SUFFIX = re.compile(r"(?:-\d+| [2-9](?: \(\d+\))?| \(\d+\))$")
TOL = 0.25
VARIABLE_SIZE_BOARDS = {"10_운영OS"}      # 데이터로 다시 그리는 대시보드 — 세대마다 크기가 다르다


def nfc(s: str) -> str:
    return unicodedata.normalize("NFC", s)


def md5(p: Path) -> str:
    h = hashlib.md5()                                          # noqa: S324
    with p.open("rb") as fh:
        for b in iter(lambda: fh.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


def stage_index(board: str) -> dict[str, tuple[str, int]]:
    """상대경로(NFC) → (md5, size). 스테이지에 있는 것만 기계 파일이다."""
    root = STAGE / board
    out = {}
    for p in root.rglob("*"):
        if p.is_file() and not p.name.startswith("."):
            out[nfc(str(p.relative_to(root)))] = (md5(p), p.stat().st_size)
    return out


def cloud_list(board: str) -> list[dict]:
    r = subprocess.run(["rclone", "lsjson", "-R", "--files-only", "--hash", f"{RO}{BASE}/{board}"],
                       capture_output=True, text=True, timeout=300)
    return json.loads(r.stdout or "[]")


def split(rel: str) -> tuple[str, str, str]:
    """'img/pms_x 2 (1).png' → ('img/', 'pms_x', '.png')  접미를 벗긴 어간."""
    d, base = os.path.split(rel)
    stem, ext = os.path.splitext(base)
    stem = SUFFIX.sub("", stem)
    return (d + "/" if d else ""), stem, ext


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    a = ap.parse_args()
    plan: list[tuple[str, str, str, str]] = []     # (action, board, rel, why)  action: delete|rename|keep
    raw: dict[tuple[str, str], str] = {}           # (board, rel) → Drive 원문 경로
    for board in BOARDS:
        st = stage_index(board)
        files = cloud_list(board)
        groups: dict[str, list[dict]] = {}
        for f in files:
            rel = nfc(f["Path"])
            if rel.split("/")[0] != rel and rel.split("/")[0] not in ("img",):
                continue                                          # 하위 폴더(KSH/ 등)는 제외
            if os.path.basename(rel).startswith("_"):
                continue                                          # _기획_*, _README 등 제외
            d, stem, ext = split(rel)
            canon = f"{d}{stem}{ext}"
            if canon not in st:
                continue                                          # 스테이지에 없는 이름 = 사람 파일. 손대지 않는다
            f["_canon"] = canon; f["_rel"] = rel; f["_raw"] = f["Path"]   # 명령엔 원문(NFD 가능) 이름
            groups.setdefault(canon, []).append(f)
        for canon, items in groups.items():
            s_md5, s_size = st[canon]
            same = [x for x in items if x.get("Hashes", {}).get("md5") == s_md5]
            # 20_정부과제시스템 은 정적 매뉴얼 페이지라 세대마다 크기가 같다 — 크기가 다르면 그것만으로
            # 이상 신호(사람 손). 10_운영OS 는 데이터로 다시 그리는 대시보드라 세대마다 크기가 변한다 → ±TOL.
            exact = board not in VARIABLE_SIZE_BOARDS
            # 정본: 스테이지와 해시가 같은 것 중 정식 이름이 있으면 그것, 없으면 가장 새것
            keep = None
            if same:
                keep = next((x for x in same if x["_rel"] == canon), None) or max(same, key=lambda x: x["ModTime"])
            for x in sorted(items, key=lambda x: x["ModTime"]):
                raw[(board, x["_rel"])] = x["_raw"]
                if x is keep:
                    if x["_rel"] != canon:
                        plan.append(("rename", board, x["_rel"], f"정식 이름 복원 → {canon}"))
                    else:
                        plan.append(("keep", board, x["_rel"], "정본 (스테이지와 해시 일치)"))
                    continue
                size = x["Size"]
                is_gen = (size == s_size) if exact else (abs(size - s_size) <= TOL * s_size)
                if x.get("Hashes", {}).get("md5") == s_md5 or is_gen:
                    plan.append(("delete", board, x["_rel"],
                                 f"옛 기계 세대 {x['ModTime'][:16]} {size}B (스테이지 {s_size}B)"))
                else:
                    plan.append(("rename", board, x["_rel"],
                                 f"★크기 {size}B ≠ 스테이지 {s_size}B — 사람 손 가능성, 보존"))
    n = {k: sum(1 for p in plan if p[0] == k) for k in ("delete", "rename", "keep")}
    print(f"{'조치':<7}{'보드':<18}{'파일':<52}사유")
    print("─" * 120)
    for act, board, rel, why in plan:
        if act == "keep":
            continue
        print(f"{ {'delete':'삭제','rename':'이름변경'}[act]:<7}{board:<18}{rel[-50:]:<52}{why}")
    print(f"\n삭제 {n['delete']} · 이름변경 {n['rename']} · 정본 유지 {n['keep']}   (스테이지에 없는 이름 = 사람 파일은 목록에 없음)")
    if not a.apply:
        print("\n[dry-run] --apply 로 실행. 삭제는 Drive 휴지통 (30일 복구).")
        return 0
    fail = 0
    for act, board, rel, why in plan:
        src = f"{RW}{BASE}/{board}/{raw.get((board, rel), rel)}"
        if act == "delete":
            r = subprocess.run(["rclone", "deletefile", src], capture_output=True, text=True)
        elif act == "rename":
            d, stem, ext = split(rel)
            if "정식 이름 복원" in why:
                dst = f"{RW}{BASE}/{board}/{d}{stem}{ext}"
            else:
                m = re.search(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}", why) or None
                tag = re.sub(r"[-:T]", "", m.group(0)) if m else "unknown"
                dst = f"{RW}{BASE}/{board}/{d}{stem}_{tag}_미확인{ext}"
            r = subprocess.run(["rclone", "moveto", src, dst], capture_output=True, text=True)
        else:
            continue
        ok = r.returncode == 0
        fail += 0 if ok else 1
        print(f"{'✓' if ok else '✗'} {act} {board}/{rel}" + ("" if ok else f"  {r.stderr.strip()[-100:]}"))
    print(f"\n완료 — 실패 {fail}")
    return 1 if fail else 0


if __name__ == "__main__":
    raise SystemExit(main())
