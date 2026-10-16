#!/usr/bin/env python3
"""주간보고 — 원장 이력의 7일 절단면. 매주 월요일 저녁에 만든다.

    python3 scripts/원장/주간보고생성.py [--기준 2026-09-14] [--dry]

왜 별도 산출물이 아닌가
    주간보고는 새로 쓰는 문서가 아니라 **이미 쌓인 이력을 한 주 단위로 자른 것**이다.
    사람이 「지난주에 무엇을 했는지」를 기억해 타이핑하는 순간 그 시스템은 죽는다
    (`project_master.csv` 가 33일간 한 줄도 늘지 않은 이유가 그것이다).

    그래서 이 생성기는 아무것도 묻지 않는다. 회차 이력의 「직전 대비 변경」을 이어 붙이고,
    캘린더 마감과 미해소 이슈를 얹는다.

대표 보고 대상
    한 주에 대표가 볼 것은 **결정을 기다리는 것과 외부 기한이 걸린 것**뿐이다.
    진행 상황 나열은 대표 몫이 아니다 (Roy 정정 2026-08-19).
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import re
from pathlib import Path

PROJ = Path(__file__).resolve().parents[2]
OUT = PROJ / "output" / "원장"
HIST = OUT / "이력"
DEFS = Path(__file__).resolve().parent / "과제정의.json"

DONE = re.compile(r"완료|끝냈|제출|발송|수령|접수|회신|이체|납부|등록했|승인(?!\s*대기)|체결|확정")
BLOCK = re.compile(r"대기|지연|보류|막혔|불가|미정|검토\s*중|확인\s*필요|요청\s*중")
PLAN = re.compile(r"예정|계획|하겠습니다|진행할|준비|예상")


def load_defs() -> list[dict]:
    return json.loads(DEFS.read_text(encoding="utf-8"))["과제"]


def week_range(base: dt.date) -> tuple[dt.date, dt.date]:
    """기준일이 속한 주의 **지난 7일**. 월요일 저녁 기준이면 지난주 월~일이다."""
    monday = base - dt.timedelta(days=base.weekday())
    return monday - dt.timedelta(days=7), monday - dt.timedelta(days=1)


def rounds_in(a: dt.date, b: dt.date) -> list[Path]:
    out = []
    for p in sorted(HIST.glob("*/*.md")):
        m = re.match(r"(\d{4}-\d{2}-\d{2})_\d{4}", p.stem)
        if m and a.isoformat() <= m.group(1) <= (b + dt.timedelta(days=1)).isoformat():
            out.append(p)
    return out


def changes_from(paths: list[Path]) -> list[tuple[str, str, str]]:
    """회차 파일의 「직전 회차 대비 변경」 절에서 신규 항목을 모은다.

    이력이 쌓이지 않았으면 비어 있고, 그 경우 원장 항목 날짜로 물러선다.
    """
    out = []
    for p in paths:
        body = p.read_text(encoding="utf-8")
        m = re.search(r"## 직전 회차 대비 변경\n(.*?)(?=\n## )", body, re.S)
        if not m:
            continue
        for line in m.group(1).splitlines():
            g = re.match(r"- \*\*신규\*\* \[(.+?)\] (.+?) `(.+?) · (\d{4}-\d{2}-\d{2})`", line)
            if g:
                out.append((g.group(1), g.group(2).strip(" *"), f"{g.group(3)} · {g.group(4)[5:]}"))
    return out


def items_from_latest(a: dt.date, b: dt.date) -> dict:
    """원장 최신본에서 그 주에 걸친 항목. 이력이 얕을 때의 대안 경로."""
    js = OUT / "최신.js"
    if not js.exists():
        return {}
    raw = js.read_text(encoding="utf-8")
    m = re.search(r"var OPS_DATA = (\{.*\});", raw, re.S)
    if not m:
        return {}
    data = json.loads(m.group(1))
    res = {}
    for p in data.get("과제", []):
        rows = [it for it in p.get("항목", [])
                if a.isoformat() <= (it.get("일자") or "")[:10] <= b.isoformat()]
        if rows:
            res[p["이름"]] = rows
    return res, data


def bucket(text: str) -> str:
    if DONE.search(text) and not BLOCK.search(text):
        return "실적"
    if BLOCK.search(text):
        return "이슈"
    if PLAN.search(text):
        return "계획"
    return "실적"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--기준", dest="base")
    ap.add_argument("--dry", action="store_true")
    a = ap.parse_args()

    base = dt.date.fromisoformat(a.base) if a.base else dt.date.today()
    start, end = week_range(base)
    year, wk, _ = start.isocalendar()
    tag = f"{year}-W{wk:02d}"
    defs = load_defs()

    paths = rounds_in(start, end)
    changes = changes_from(paths)
    latest, meta = items_from_latest(start, end) if (OUT / "최신.js").exists() else ({}, {})

    L = ["---", f"주차: {tag}", f"기간: {start} ~ {end}",
         f"생성: {dt.datetime.now():%Y-%m-%dT%H:%M}+09:00",
         f"근거 회차: {len(paths)}개", f"원천: {json.dumps(meta.get('원천', {}), ensure_ascii=False)}",
         "---", "",
         f"# 연구기획사업부 주간보고 · {tag}", "",
         f"{start:%m/%d}(월) ~ {end:%m/%d}(일). 회차 이력 {len(paths)}개에서 자동 생성했습니다. "
         "사람이 입력한 것은 없습니다.", ""]

    if len(paths) < 5:
        L += ["> **주의**: 이 주의 회차 이력이 "
              f"{len(paths)}개뿐입니다(평일 정상 15개). 변경 추적이 얕아 원장 항목 날짜로 대신했습니다. "
              "회차가 쌓이면 정확해집니다.", ""]

    # ---- 과제별
    L.append("## 과제별\n")
    for p in defs:
        rows = latest.get(p["이름"], [])
        ch = [c for c in changes if c[0] == p["이름"]]
        if not rows and not ch:
            continue
        L.append(f"### {p['no']}. {p['이름']}  `{p['구분']}`")
        buckets = {"실적": [], "계획": [], "이슈": []}
        chat = 0
        for it in rows:
            # 잡담·봇 명령은 세어서 한 줄로 접는다. 무엇이 잡담인지는 원장이 정한다
            # (원장생성.py mark_work). 화면과 같은 판정을 써야 둘이 어긋나지 않는다.
            if it.get("업무") is False:
                chat += 1
                continue
            buckets[bucket(it["문장"])].append(
                f"- [{it['일자'][5:]}] {it['문장']} `{it['출처']}`")
        for k in ("실적", "계획", "이슈"):
            if buckets[k]:
                L.append(f"\n**{k}**")
                L += buckets[k][:12]
                if len(buckets[k]) > 12:
                    L.append(f"- _외 {len(buckets[k]) - 12}건_")
        if chat:
            L.append(f"\n_방 대화 {chat}건은 잡담·봇 명령이라 뺐습니다. 원장에는 있습니다._")
        L.append("")

    # ---- 대표 보고 대상
    L += ["## 대표 보고 대상\n",
          "결정을 기다리는 것과 외부 기한이 걸린 것만 올립니다. 진행 나열은 넣지 않습니다.\n"]
    # 같은 사안이 두 방에 중복 게시되므로 문구로 한 번만 남긴다.
    ceo, seen = [], set()
    for name, rows in latest.items():
        for it in rows:
            if not (BLOCK.search(it["문장"]) and it.get("신뢰도") == "ledger"):
                continue
            k = (name, re.sub(r"[^0-9가-힣A-Za-z]", "", it["문장"])[:50])
            if k in seen:
                continue
            seen.add(k)
            ceo.append(f"- **{name}** — {it['문장']} `{it['출처']} · {it['일자'][5:]}`")
    if ceo:
        L += ceo[:15]
        if len(ceo) > 15:
            L.append(f"- _외 {len(ceo) - 15}건_")
    else:
        L.append("_이번 주 대표 결정을 기다리는 항목이 없습니다._")

    # ---- 신뢰
    L += ["", "## 이 보고서가 보는 범위\n"]
    src = meta.get("원천", {})
    if src:
        L += ["| 원천 | 반영 시각 |", "|---|---|"]
        L += [f"| {k} | {v or '읽기 실패'} |" for k, v in src.items()]
    miss = meta.get("결손", [])
    if miss:
        L += ["", f"**결손 회차 {len(miss)}건**: {', '.join(miss[:10])}"
              + (f" 외 {len(miss) - 10}건" if len(miss) > 10 else ""),
              "그 사이의 변화는 다음 회차에 한꺼번에 들어 있습니다."]
    unl = len(meta.get("미분류", []))
    if unl:
        L += ["", f"**미분류 관측 {unl}건** — 어느 과제에도 귀속되지 않았습니다. "
              "추측해서 붙이지 않았습니다."]

    doc = "\n".join(L)
    print(f"· {tag} ({start} ~ {end}) · 회차 {len(paths)} · 과제 {len(latest)} · 대표보고 {len(ceo)}")
    if a.dry:
        print("  (dry)")
        return 0
    d = OUT / "주간"
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{tag}.md").write_text(doc, encoding="utf-8")
    print(f"  → output/원장/주간/{tag}.md")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
