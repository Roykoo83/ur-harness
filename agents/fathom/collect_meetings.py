#!/usr/bin/env python3
"""Fathom 회의 전사를 수집하고 회의록 초안 골격을 만든다.

    python3 collect_meetings.py                 # 최근 14일
    python3 collect_meetings.py --since 2026-08-01
    python3 collect_meetings.py --rid 171949500 # 특정 녹화만

산출물
    data/meetings.json                 회의 색인 (누적)
    data/transcripts/<rid>.txt         원문 전사
    output/minutes/<날짜>_<유형>.draft.md  회의록 초안 (사람/AI가 다듬을 골격)

회의록 본문은 **여기서 자동 생성하지 않는다.** 전사 품질이 한국어에서 낮아
문맥 교정이 필요하기 때문이다. 이 스크립트는 골격과 이월 액션까지만 채우고,
본문 정리는 Claude가 `templates/회의록_양식.md` 규칙에 따라 수행한다.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from fathom_client import FathomError, get_client  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
DATA, TRANS = ROOT / "data", ROOT / "data" / "transcripts"
MIN = ROOT / "output" / "minutes"
IDX, ACTIONS = DATA / "meetings.json", DATA / "actions.json"

# 회의 유형 판정 — 제목·참석자로 우리 회의 체계에 사상한다.
TYPES = [
    ("전사주간회의", ("전사", "주간회의", "주간업무보고", "weekly", "impromptu zoom")),
    ("부서랩업미팅", ("랩업", "wrap", "부서미팅", "팀 미팅", "팀미팅")),
    ("PMO회의", ("pmo",)),
    ("과제회의", ("중간보고", "착수", "킥오프", "kick", "협약")),
]


def log(m: str) -> None:
    print(f"[{dt.datetime.now():%H:%M:%S}] {m}", flush=True)


def classify(title: str) -> str:
    t = (title or "").lower()
    for name, hints in TYPES:
        if any(h.lower() in t for h in hints):
            return name
    return "기타회의"


def meeting_id(date: str, mtype: str) -> str:
    prefix = {"전사주간회의": "WM", "부서랩업미팅": "DW",
              "PMO회의": "PMO", "과제회의": "PRJ"}.get(mtype, "MTG")
    return f"{prefix}-{date}"


def speakers(transcript: str) -> list[str]:
    names = re.findall(r"^\[[^\]]*\]\([^)]*\)\s*([^:]{1,20}):", transcript, re.M)
    if not names:
        names = re.findall(r"^\[[^\]]*\]\s*([^:]{1,20}):", transcript, re.M)
    seen, out = set(), []
    for n in names:
        n = n.strip()
        if n and n not in seen:
            seen.add(n)
            out.append(n)
    return out


def open_actions(before: str) -> list[dict]:
    """이전 회의에서 아직 닫히지 않은 액션 — 롱레인지 연결의 핵심."""
    if not ACTIONS.exists():
        return []
    all_a = json.loads(ACTIONS.read_text(encoding="utf-8"))
    return [a for a in all_a
            if a.get("status") != "done" and a.get("meeting_date", "") < before]


def draft(meta: dict, transcript: str, carry: list[dict], prev: str | None) -> str:
    spk = speakers(transcript)
    carry_rows = "\n".join(
        f"| {a['action']} | {a.get('owner','-')} | {a.get('due','-')} | "
        f"{a['meeting_date']} | ☐ |" for a in carry) or "| (없음) | | | | |"
    return f"""---
meeting_id: {meta['meeting_id']}
type: {meta['type']}
date: {meta['date']}
recording_id: {meta['recording_id']}
source: {meta['url']}
sensitivity: 사내한정
prev: {prev or '-'}
next: -
status: draft
---

# {meta['type']} 회의록 — {meta['date']}

> ⚠️ **초안입니다.** 자동 전사 원문을 옮긴 상태이며 아직 검수되지 않았습니다.
> 한국어 음성 인식 오류가 많으므로 반드시 문맥 교정 후 `status: final` 로 바꾸십시오.

## 1. 개요

| 항목 | 내용 |
|---|---|
| 일시 | {meta['date']} |
| 회의 유형 | {meta['type']} |
| 원본 | [Fathom 녹화]({meta['url']}) |

## 2. 참석자

{', '.join(spk) if spk else '(전사에서 화자를 찾지 못했습니다)'}

> 회의실 공용 계정으로 접속하면 여러 사람이 한 화자로 합쳐집니다. 확인 후 수정하십시오.

## 3. 논의 내용

<!-- 여기를 채웁니다. 아래 원문을 근거로 하되, 결정과 액션이 드러나게 재구성합니다. -->

## 4. 결정 사항

| # | 결정 | 주체 |
|---|---|---|
| 1 | | |

## 5. 향후 일정 (액션 아이템)

| # | 액션 | 담당 | 기한 | 상태 |
|---|---|---|---|---|
| 1 | | | | ☐ |

## 6. 지난 회의 이월 안건

| 이월 액션 | 담당 | 기한 | 최초 제기 | 처리 |
|---|---|---|---|---|
{carry_rows}

## 7. 다음 회의로 이월

-

---

## 부록 — 자동 전사 원문

<details><summary>펼쳐보기 ({len(transcript):,}자)</summary>

```
{transcript}
```

</details>
"""


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--since")
    ap.add_argument("--days", type=int, default=14)
    ap.add_argument("--rid")
    ap.add_argument("--mcp", action="store_true", help="API 키가 있어도 MCP 경로를 쓴다")
    args = ap.parse_args()

    since = args.since or (dt.date.today() - dt.timedelta(days=args.days)).isoformat()
    for d in (DATA, TRANS, MIN):
        d.mkdir(parents=True, exist_ok=True)

    cli = get_client(prefer_api=not args.mcp)
    log(f"인증 방식: {cli.mode} · 기준일 {since}")

    try:
        meetings = cli.list_meetings(created_after=f"{since}T00:00:00Z")
    except FathomError as ex:
        log(f"회의 목록 조회 실패: {ex}")
        return 2
    if args.rid:
        meetings = [m for m in meetings if str(m.get("recording_id")) == str(args.rid)]
    log(f"대상 회의 {len(meetings)}건")

    index = json.loads(IDX.read_text(encoding="utf-8")) if IDX.exists() else {}
    made = 0
    for m in sorted(meetings, key=lambda x: x.get("date") or ""):
        rid = str(m.get("recording_id"))
        date = (m.get("date") or "")[:10]
        title = m.get("title") or ""
        mtype = classify(title)
        mid = meeting_id(date, mtype)

        tpath = TRANS / f"{rid}.txt"
        if tpath.exists():
            tx = tpath.read_text(encoding="utf-8")
        else:
            try:
                tx = cli.transcript(rid)
            except FathomError as ex:
                log(f"  전사 실패 {rid}: {ex}")
                continue
            tpath.write_text(tx, encoding="utf-8")
        log(f"· {date} {mtype} ({len(tx):,}자) {title[:40]}")

        prev = sorted((v["meeting_id"] for v in index.values()
                       if v["type"] == mtype and v["date"] < date), reverse=True)
        meta = {"meeting_id": mid, "type": mtype, "date": date,
                "recording_id": rid, "url": m.get("url", ""), "title": title}
        index[rid] = {**meta, "chars": len(tx),
                      "recorded_by": m.get("recorded_by", "")}

        out = MIN / f"{date}_{mtype}.draft.md"
        if not out.exists() and not (MIN / f"{date}_{mtype}.md").exists():
            out.write_text(draft(meta, tx, open_actions(date),
                                 prev[0] if prev else None), encoding="utf-8")
            made += 1
            log(f"  → 초안 생성 {out.name}")
        else:
            log("  → 회의록이 이미 있어 건너뜀")

    IDX.write_text(json.dumps(index, ensure_ascii=False, indent=1), encoding="utf-8")
    if hasattr(cli, "close"):
        cli.close()
    log(f"완료 · 색인 {len(index)}건 · 신규 초안 {made}건")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
