#!/usr/bin/env python3
"""원장이 얼마나 맞는지 잰다. 정답지는 **사람이 손으로 채운 표**다.

    python3 scripts/원장/정확도측정.py [--정답지 <ksh.html>]

왜 {{MEMBER_B}} 리더의 결정판이 정답지인가
    그는 2026-09-04 에 U 드라이브를 **전수 확인해** §01 표를 손으로 채웠다.
    표준협회 30건, MeDIAuto 2건, TIPS 글로벌트랙과 팁스 창업사업화는 갱신 0건까지
    사람이 직접 확인한 결과다. 기계가 재현해야 할 것이 정확히 그것이다.

무엇을 재는가
    · **귀속 정확도** — 같은 항목을 같은 과제에 붙였는가. 목표 98%
    · **재현율** — 그가 적은 것 중 기계가 잡은 비율. **목표가 아니라 공개 지표다.**
      재현율을 목표로 걸면 추론을 늘리게 되고 그 순간 정확도가 무너진다.
    · **잡음** — 기계에만 있고 사람 표에 없는 것. 사람 판단이 필요하다

95% 를 어디에 거는가
    화면에 뜬 것이 맞는 비율(귀속)에 건다. 못 본 것은 못 봤다고 적는다.
"""
from __future__ import annotations

import argparse
import datetime as dt
import html
import json
import re
import unicodedata
from pathlib import Path

PROJ = Path(__file__).resolve().parents[2]
OUT = PROJ / "output" / "원장"
DEFS = Path(__file__).resolve().parent / "과제정의.json"
DEFAULT_TRUTH = (PROJ / "workspace" / "재정비" / "정답지"
                 / "rpbd_운영OS_대시보드_ksh.html")

nfc = lambda s: unicodedata.normalize("NFC", s or "")          # noqa: E731


def norm(s: str) -> str:
    """비교용 골자. 조사·기호·공백을 걷어낸다."""
    s = nfc(s)
    s = re.sub(r"<[^>]+>", " ", s)
    s = html.unescape(s)
    return re.sub(r"[^0-9a-z가-힣]", "", s.lower())


def shingles(s: str, k: int = 3) -> set:
    return {s[i:i + k] for i in range(max(0, len(s) - k + 1))}


def sim(a: str, b: str) -> float:
    A, B = shingles(a), shingles(b)
    if not A or not B:
        return 0.0
    return len(A & B) / min(len(A), len(B))


def load_truth(path: Path, defs) -> list[dict]:
    """결정판 §01 표에서 (과제, 항목, 배지) 를 뽑는다."""
    doc = path.read_text(encoding="utf-8")
    m = re.search(r'<section[^>]*id="s1".*?(?=<section[^>]*id="s2")', doc, re.S)
    if not m:
        return []
    rows = re.findall(r"<tr[^>]*>(.*?)</tr>", m.group(0), re.S)
    out = []
    for r in rows:
        tds = re.findall(r"<td[^>]*>(.*?)</td>", r, re.S)
        if len(tds) < 6:
            continue
        pjt = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", tds[2])).strip()
        key = match_project(pjt, defs)
        for col, kind in ((tds[3], "실적"), (tds[4], "계획"), (tds[5], "이슈")):
            for li in re.findall(r"<li[^>]*>(.*?)</li>", col, re.S) or [col]:
                badge = re.findall(r'<span class="mini (\w+)">([^<]*)</span>', li)
                text = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", li)).strip()
                text = html.unescape(text)
                for _, b in badge:
                    text = text.replace(b, "")
                text = re.sub(r"\s+", " ", text).strip()
                if len(text) < 8:
                    continue
                out.append({"과제": key, "PJT": pjt, "칸": kind, "문장": text,
                            "배지": [b[0] for b in badge] or ["none"]})
    return out


def match_project(pjt: str, defs) -> str | None:
    t = norm(pjt)
    best = None
    for p in defs:
        for a in p["별칭"] + [p["이름"]]:
            n = norm(a)
            if n and n in t and (best is None or len(n) > best[1]):
                best = (p["key"], len(n))
    return best[0] if best else None


def load_ledger() -> tuple[dict, dict]:
    js = OUT / "최신.js"
    if not js.exists():
        return {}, {}
    m = re.search(r"var OPS_DATA = (\{.*\});", js.read_text(encoding="utf-8"), re.S)
    data = json.loads(m.group(1))
    by = {}
    for p in data.get("과제", []):
        by[p["key"]] = p.get("항목", [])
    return by, data


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--정답지", dest="truth", default=str(DEFAULT_TRUTH))
    a = ap.parse_args()

    tp = Path(a.truth)
    if not tp.exists():
        print(f"!! 정답지를 찾을 수 없습니다: {tp}")
        return 1

    defs = json.loads(DEFS.read_text(encoding="utf-8"))["과제"]
    truth = load_truth(tp, defs)
    ledger, meta = load_ledger()
    if not ledger:
        print("!! 원장(최신.js)이 없습니다")
        return 1

    # ---- 재현: 정답지 항목이 원장에 있는가
    hit, miss, wrong = [], [], []
    for t in truth:
        tn = norm(t["문장"])
        if len(tn) < 8:
            continue
        best, bkey = 0.0, None
        for key, rows in ledger.items():
            for it in rows:
                s = sim(tn, norm(it["문장"]))
                if s > best:
                    best, bkey = s, key
        if best >= 0.55:
            (hit if bkey == t["과제"] else wrong).append((t, bkey, round(best, 2)))
        else:
            miss.append(t)

    tot = len(hit) + len(wrong) + len(miss)
    found = len(hit) + len(wrong)
    attr = (len(hit) / found * 100) if found else 0.0
    recall = (found / tot * 100) if tot else 0.0

    print(f"정답지: {tp.name}  항목 {tot}건")
    print(f"· 귀속 정확도  {attr:5.1f}%   ({len(hit)}/{found})   ← 95% 기준은 여기")
    print(f"· 재현율      {recall:5.1f}%   ({found}/{tot})       ← 목표 아님. 공개 지표")
    print(f"· 못 잡음      {len(miss)}건")

    # 배지별 재현
    from collections import Counter
    bm = Counter()
    bt = Counter()
    for t in truth:
        b = t["배지"][0]
        bt[b] += 1
    for t in miss:
        bm[t["배지"][0]] += 1
    print("\n배지별 (사람이 붙인 근거 등급)")
    for b in ("led", "obs", "unk", "none"):
        if bt[b]:
            print(f"   {b:5} 정답지 {bt[b]:3}건 · 못 잡음 {bm[b]:3}건 "
                  f"· 재현 {(bt[b]-bm[b])/bt[b]*100:5.1f}%")

    if wrong:
        print(f"\n귀속 오류 {len(wrong)}건")
        for t, k, s in wrong[:10]:
            print(f"   사람 {t['과제']} vs 기계 {k}  ({s})  {t['문장'][:56]}")

    print(f"\n못 잡은 것 {len(miss)}건 중 10건")
    for t in miss[:10]:
        print(f"   [{t['과제'] or '미상'}·{t['칸']}·{t['배지'][0]}] {t['문장'][:64]}")

    rec = {"측정": dt.datetime.now().strftime("%Y-%m-%d %H:%M"),
           "정답지": tp.name, "표본": tot,
           "귀속정확도": round(attr, 1), "재현율": round(recall, 1),
           "귀속오류": len(wrong), "못잡음": len(miss),
           "원장원천": meta.get("원천", {})}
    d = PROJ / "workspace" / "재정비" / "정확도"
    d.mkdir(parents=True, exist_ok=True)
    (d / f"측정_{dt.date.today()}.json").write_text(
        json.dumps(rec, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"\n→ workspace/재정비/정확도/측정_{dt.date.today()}.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
