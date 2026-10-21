#!/usr/bin/env python3
"""차주 주간보고 pptx 초안 — 최신 제출본을 복사해 **네이티브로** 칸만 바꾼다.

    python3 scripts/tasks/make_weekly_pptx.py

원본(제출본)은 읽기 전용. 결과는
    output/weekly_draft/[연구기획사업] 주간업무보고_<YYMMDD>.pptx
    SSOT {{DEPT_ROOT}}/00_부서정본/주간보고_초안/  (같은 파일 + 지침 README)

서식 보존 규칙: 셀 텍스트는 기존 첫 run 의 글꼴을 물려받아 다시 쓴다.
슬라이드를 이미지로 대체하거나 표를 새로 그리지 않는다 (전역 규칙: PPTX는 항상 네이티브).
"""
from __future__ import annotations

import datetime as dt
import json
import re
import shutil
import sys
from copy import deepcopy
from pathlib import Path

from pptx import Presentation

PROJ = Path(__file__).resolve().parents[2]
ATT = _weekly_dir("attachments")
DRAFT = PROJ / "data" / "tasks" / "weekly_draft.json"
OUTDIR = PROJ / "output" / "weekly_draft"
SSOTDIR = (Path.home() / "{{MOUNT_REL}}"
           / "공유 드라이브" / "{{SSOT_DRIVE}}" / "{{DEPT_ROOT}}" / "00_부서정본"
           / "주간보고_초안")




def _rclone() -> str | None:
    """rclone 경로. 못 찾으면 None — 「없음」을 가짜 경로로 숨기지 않는다."""
    import sys as _s
    from pathlib import Path as _P
    _s.path.insert(0, str(_P(__file__).resolve().parents[2] / "core"))
    from lib.runtime import find_exe                          # noqa: PLC0415
    return find_exe("rclone", env_var="RCLONE_BIN")


def _weekly_dir(sub: str = "") -> Path:
    """주간보고 데이터 경로 — 설정의 작업공간 아래. 코드 경로와 섞지 않는다."""
    import sys as _s
    _s.path.insert(0, str(Path(__file__).resolve().parents[2] / "core"))
    from lib.config import paths                                 # noqa: PLC0415
    return paths.weekly / sub if sub else paths.weekly


def latest_pptx(before: str = "999999") -> Path | None:
    """직전 제출본을 바탕으로 한다 — 이번 주기에 이미 올라온 본은 기준이 아니다."""
    import re as _re
    cands = []
    for p in ATT.rglob("[[]연구기획사업[]]*주간업무보고_*.pptx"):
        m = _re.search(r"_(\d{6})", p.name)
        if m and m.group(1) <= before:
            cands.append((m.group(1), p))
    return max(cands)[1] if cands else None


def set_cell(cell, text: str) -> None:
    """첫 문단·첫 run 의 서식을 본으로 삼아 줄 단위로 다시 쓴다."""
    tf = cell.text_frame
    lines = text.split("\n") or [""]
    first = tf.paragraphs[0]
    proto = deepcopy(first.runs[0]._r) if first.runs else None
    # 기존 문단 모두 제거 후 첫 문단만 남긴다
    for p in list(tf.paragraphs[1:]):
        p._p.getparent().remove(p._p)
    for r in list(first.runs):
        r._r.getparent().remove(r._r)
    def add_run(para, s):
        if proto is not None:
            r = deepcopy(proto)
            para._p.append(r)
            # run 안의 기존 텍스트 노드를 교체
            for t in r.findall(".//{http://schemas.openxmlformats.org/drawingml/2006/main}t"):
                t.text = s
        else:
            para.add_run().text = s  # type: ignore[attr-defined]
    add_run(first, lines[0])
    for ln in lines[1:]:
        p = tf.add_paragraph()
        p.alignment = first.alignment
        add_run(p, ln)


def krange(a: str, b: str) -> str:
    return f"(2026.{a[5:7]}.{a[8:10]}~{b[5:7]}.{b[8:10]})"


def main() -> int:
    if not DRAFT.exists():
        print("초안 데이터 없음 — weekly_draft.py 먼저")
        return 1
    d = json.loads(DRAFT.read_text(encoding="utf-8"))
    base = (d.get("base_week") or "").replace("-", "")[2:] or "999999"
    src = latest_pptx(before=base)
    if not src:
        print("원본 pptx 없음")
        return 1
    rpt = dt.date.fromisoformat(d["report_date"])
    ymd = f"{rpt:%y%m%d}"
    if ymd in src.name:
        print(f"원본이 이미 {ymd} 본 — 생성 생략")
        return 0

    OUTDIR.mkdir(parents=True, exist_ok=True)
    out = OUTDIR / f"[연구기획사업] 주간업무보고_{ymd}.pptx"
    shutil.copy2(src, out)

    pr = Presentation(str(out))
    # 표지 날짜
    for sh in pr.slides[0].shapes:
        if sh.has_text_frame and re.search(r"20\d{2}\.\d{2}\.\d{2}", sh.text_frame.text):
            for para in sh.text_frame.paragraphs:
                for run in para.runs:
                    run.text = re.sub(r"20\d{2}\.\d{2}\.\d{2}", f"{rpt:%Y.%m.%d}", run.text)

    # 본문 표
    table = None
    for sh in pr.slides[1].shapes:
        if sh.has_table:
            table = sh.table
            break
    if table is None:
        print("표를 찾지 못함 — 중단")
        return 1

    # 머리행 날짜 구간
    prev_r = krange(d["week_from"], d["week_to"])
    this_to = (rpt + dt.timedelta(days=4)).isoformat()
    this_r = krange(d["report_date"], this_to)
    for ci, cell in enumerate(table.rows[0].cells):
        t = cell.text
        if "전주" in t:
            set_cell(cell, re.sub(r"\(.*\)", prev_r, t.replace("\x0b", "\n")))
        elif "금주" in t:
            set_cell(cell, re.sub(r"\(.*\)", this_r, t.replace("\x0b", "\n")))

    # 데이터 행: PJT 텍스트로 초안 행을 찾는다 (행 순서가 곧 양식이다 — 옮기지 않는다)
    def norm(s):
        return re.sub(r"[^0-9a-z가-힣]", "", s.lower())
    rows_by_pjt = {norm(r["pjt"]): r for r in d["rows"] if r["pjt"]}
    filled = 0
    for row in list(table.rows)[1:]:
        pjt = norm(row.cells[2].text)
        hit = rows_by_pjt.get(pjt) or next(
            (r for k, r in rows_by_pjt.items() if k and (k in pjt or pjt in k)), None)
        if not hit:
            continue
        set_cell(row.cells[3], hit["전주"])
        set_cell(row.cells[4], hit["금주"])
        set_cell(row.cells[5], hit["이슈"] or "")
        filled += 1

    pr.save(str(out))
    # 2026-09-08 (M 안, 구역표): 주간보고_초안 은 **기계 구역 · API 단일 쓰기**다. 그 전까지는
    # 마운트에도 shutil.copy2 로 복사해 DriveFS 와 API 가 같은 파일을 둘 다 올렸고, 그 결과
    # 「 2」「 3」「 4」.pptx 충돌 사본이 매 실행마다 생겼다 (9/7~9/8 실측). 마운트는 건드리지 않는다 —
    # DriveFS 가 클라우드 정본을 알아서 내려준다.
    where = "주간보고_초안/"
    # 클라우드 정본은 API 직행 — 로컬 마운트 동기화는 조용히 유실될 수 있다 (E24·E26)
    # 2026-09-05: 서비스계정 원격 + 도착 해시검증 (lib/ssot_io). gdrive 는 쿼터 포화.
    rclone = _rclone()
    import subprocess
    r = subprocess.run(
        [rclone, "copyto", str(out),
         f"SSOT_rw:{{DEPT_ROOT}}/00_부서정본/주간보고_초안/{out.name}",
         "-q"], capture_output=True, text=True, timeout=120)
    where += " · ☁" if r.returncode == 0 else " · !클라우드 실패"
    print(f"· pptx 초안 — {out.name} · 표 {filled}행 채움 · {where}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
