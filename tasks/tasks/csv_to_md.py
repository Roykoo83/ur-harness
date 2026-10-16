#!/usr/bin/env python3
"""`project_master.csv` 를 `과제_원장.md` 표로 바꾼다 (P0, 1회성).

    python3 scripts/tasks/csv_to_md.py

왜 — csv 는 드라이브에서 미리보기가 안 되고, 열면 엑셀이 뜨고, 열면 누군가 고치고
싶어진다. 실제로 이 파일은 **7월 3일 이후 손대지 않았다.** 같은 내용의 살아 있는 정본은
이미 SSOT 원장(`data/records/project/*.jsonl`)에 있다.

md 로 바꾸면 어디서나 읽히고 diff 가 보인다. **원본은 지우지 않는다** — 옮겼다는 사실만
남기고 파일은 그대로 둔다. 지우는 것은 되돌릴 수 없다.
"""
from __future__ import annotations

import csv
import datetime as dt
import json
from pathlib import Path

PROJ = Path(__file__).resolve().parents[2]
REPO = PROJ.parents[1]
SRC = REPO / "inbox" / "연구기획_프로젝트_아카이브" / "reference" / "project_master.csv"
OUT = PROJ / "output" / "과제_원장.md"
LEDGER = PROJ / "data" / "records" / "project"

# 표에 세울 열. 20열 전부 늘어놓으면 읽히지 않는다 — 사람이 실제로 찾는 것만 남긴다.
COLS = [("project_id", "ID"), ("약칭", "과제"), ("부처", "부처"), ("전담기관", "전담기관"),
        ("협약시작", "시작"), ("협약종료", "종료"), ("총사업비_천원", "총사업비"),
        ("현재단계", "단계"), ("상태확신도", "확신도")]


def money(v: str) -> str:
    """천원 단위 정수를 읽기 쉬운 금액으로. 빈 값은 `—` (0 이 아니라 모름)."""
    try:
        n = int(str(v).replace(",", ""))
    except (TypeError, ValueError):
        return "—"
    return f"{n / 100000:,.1f}억" if n >= 100000 else f"{n:,}천원"


def live_ledger() -> tuple[int, str] | None:
    files = sorted(LEDGER.glob("*.jsonl")) if LEDGER.is_dir() else []
    if not files:
        return None
    latest = files[-1]
    n = sum(1 for line in latest.read_text(encoding="utf-8").splitlines() if line.strip())
    return n, latest.stem


def main() -> int:
    if not SRC.exists():
        print(f"원본 없음: {SRC}")
        return 2
    rows = list(csv.DictReader(SRC.read_text(encoding="utf-8").splitlines()))
    mtime = dt.datetime.fromtimestamp(SRC.stat().st_mtime)
    stale = (dt.date.today() - mtime.date()).days
    live = live_ledger()

    out = [
        "---",
        'title: "과제 원장"',
        f"source: \"{SRC.relative_to(REPO)}\"",
        f"source_updated: \"{mtime:%Y-%m-%d}\"",
        f"generated: \"{dt.datetime.now():%Y-%m-%dT%H:%M:%S+0900}\"",
        'sensitivity: "internal"',
        "---",
        "",
        "# 과제 원장",
        "",
        f"> ⚠️ **이 표는 정본이 아니다.** 원본 csv 는 {mtime:%Y-%m-%d} 이후 **{stale}일간**",
        "> 손대지 않았다. 살아 있는 정본은 SSOT 원장이다"
        + (f" — `data/records/project/{live[1]}.jsonl` (**{live[0]}건**, 매주 갱신)." if live else "."),
        "> 여기 표는 **읽기 편하라고 옮겨 둔 사본**이며, 고칠 일이 생기면 원장을 고친다.",
        "",
        "| " + " | ".join(h for _, h in COLS) + " |",
        "|" + "|".join("---" for _ in COLS) + "|",
    ]
    for r in rows:
        cells = []
        for key, _ in COLS:
            v = (r.get(key) or "").strip()
            cells.append(money(v) if key == "총사업비_천원" else (v or "—"))
        out.append("| " + " | ".join(cells) + " |")

    out += ["", "## 비고", ""]
    for r in rows:
        note = (r.get("비고") or "").strip()
        if note:
            out.append(f"- **{(r.get('약칭') or r.get('project_id') or '?').strip()}** — {note}")

    out += [
        "", "## 여기 없는 것", "",
        "| 찾는 것 | 실제 위치 |", "|---|---|",
        "| 살아 있는 과제 정본 | `rpbd-infra/data/records/project/` (주 1회 갱신) |",
        "| 과제별 예산·집행 | `U/2. R&D 사업`, `U/4. R&D 예산_통합` |",
        "| 참여인력·참여율 | `gov-system-manuals/output/people.html` |",
        "",
    ]
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text("\n".join(out) + "\n", encoding="utf-8")
    print(f"· {OUT.relative_to(PROJ)} — 과제 {len(rows)}건")
    print(f"  원본 {SRC.name} 은 {stale}일째 정체 (지우지 않고 그대로 둔다)")
    if live:
        print(f"  살아 있는 정본: data/records/project/{live[1]}.jsonl ({live[0]}건)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
