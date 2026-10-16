#!/usr/bin/env python3
"""부서별 주간 운영 대시보드를 생성한다.

RPBD 운영 OS 대시보드와 같은 디자인·같은 구조를 쓴다.
주간보고 표의 열을 다음으로 사상한다:

    전주 진행사항 / 전주 업무 실적   → P (지난 마일스톤)
    금주 진행계획 / 금주 업무 계획   → F (다가올 마일스톤)
    이슈 사항 및 해결 방안          → 이슈
    의사 결정 사항                  → 결정 필요

    python3 build_dashboards.py
"""
from __future__ import annotations

import datetime as dt
import html
import json
import re
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import design_system as ds  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
PARSED, OUT = ROOT / "data" / "parsed", ROOT / "output"
INDEX = ROOT / "data" / "weekly_index.json"

# 열 이름은 부서마다 다르다. 부분 일치로 사상한다.
COLMAP = [
    ("past", ("전주", "지난주", "실적")),
    ("future", ("금주", "이번주", "계획")),
    ("issue", ("이슈", "문제", "해결")),
    ("decision", ("의사 결정", "의사결정", "결정 사항")),
    ("kind", ("구분", "분류")),
    ("project", ("PJT", "프로젝트", "과제", "업무")),
    ("link", ("링크", "URL")),
    ("no", ("NO", "번호")),
]
KIND_CLASS = {"매출": "sales", "R&D": "rnd", "RA": "ra", "AX": "ax"}

# 연구기획사업부는 메일·메신저·드라이브까지 묶은 운영 OS 대시보드가 이미 있다.
# 컨플루언스 주간보고만으로 별도 화면을 만들면 두 벌이 되므로 링크로 넘긴다.
EXTERNAL = {
    "연구기획사업부": "../../rpbd-infra/output/rpbd_운영OS_대시보드.html",
}


def slug(org: str) -> str:
    """조직명을 파일명으로. 「개발사업부/연구」처럼 슬래시가 든 이름이 있다."""
    return org.replace("/", "_").replace(" ", "")


def e(s) -> str:
    return html.escape(str(s or "")).replace("\n", "<br>")


def map_columns(header: list[str]) -> dict[int, str]:
    """헤더 인덱스 → 표준 키. 먼저 잡힌 규칙이 이긴다."""
    out, taken = {}, set()
    for key, hints in COLMAP:
        for i, h in enumerate(header):
            if i in out:
                continue
            hn = (h or "").replace(" ", "")
            if key in taken:
                break
            if any(hint.replace(" ", "") in hn for hint in hints):
                out[i], _ = key, taken.add(key)
                break
    return out


def rows_to_items(rec: dict) -> list[dict]:
    """파싱된 표 → 표준 항목. 병합셀로 비어 있는 구분·NO는 위 행에서 이어받는다."""
    header = rec.get("header") or []
    cmap = map_columns(header)
    items, carry = [], {}
    for raw in rec.get("items", []):
        vals = list(raw.values())
        row = {}
        for i, v in enumerate(vals):
            k = cmap.get(i)
            if k:
                row[k] = (v or "").strip()
        if not row.get("project") and not row.get("past") and not row.get("future"):
            continue
        for k in ("kind", "no"):
            if row.get(k):
                carry[k] = row[k]
            elif carry.get(k):
                row[k] = carry[k]
        items.append(row)
    return items


def bullets(text: str) -> list[str]:
    """PPTX 셀의 ' / ' 구분과 줄바꿈을 항목으로 쪼갠다."""
    if not text:
        return []
    parts = re.split(r"\s*/\s*|\n+", text)
    return [p.strip(" -·•") for p in parts if p.strip(" -·•")]


def render_card(it: dict) -> str:
    kind = it.get("kind", "").strip()
    cls = KIND_CLASS.get(kind, "etc")
    past, future = bullets(it.get("past", "")), bullets(it.get("future", ""))
    issue, dec = bullets(it.get("issue", "")), bullets(it.get("decision", ""))

    chips = []
    if dec:
        chips.append('<span class="chip hot">결정 필요</span>')
    elif issue:
        chips.append('<span class="chip">이슈</span>')
    elif future:
        chips.append('<span class="chip ok">진행</span>')

    prog = ""
    if past or future:
        li = "".join(f'<li><span class="tag p">P</span><span>{e(x)}</span></li>' for x in past)
        li += "".join(f'<li><span class="tag f">F</span><span>{e(x)}</span></li>' for x in future)
        prog = ('<div class="block"><div class="block-t"><span class="dot"></span>'
                f'진행사항 — 보고</div><ul class="items">{li}</ul></div>')

    isu = ""
    if issue or dec:
        li = "".join(f'<li><span class="tag c">결정</span><span>{e(x)}</span></li>' for x in dec)
        li += "".join(f'<li><span class="tag d">이슈</span><span>{e(x)}</span></li>' for x in issue)
        isu = ('<div class="block"><div class="block-t issue"><span class="dot"></span>'
               f'이슈사항 — 결정 필요</div><ul class="items">{li}</ul></div>')

    link = it.get("link", "")
    meta = f'<div class="meta"><a href="{e(link)}" style="color:inherit">{e(link[:70])}</a></div>' \
        if link.startswith("http") else ""

    return f"""<div class="card">
  <div class="card-head">
    <div style="flex:1 1 auto">
      <div class="id"><span class="kind {cls}">{e(kind or "기타")}</span></div>
      <h4>{e(it.get("project") or "(항목명 없음)")}</h4>{meta}
    </div>{"".join(chips)}
  </div>{prog}{isu}
</div>"""


def render_org(org: str, records: list[dict], all_weeks: list[str]) -> str:
    records = sorted(records, key=lambda r: r["week"], reverse=True)
    latest = records[0]
    items = rows_to_items(latest)

    n_issue = sum(1 for i in items if i.get("issue"))
    n_dec = sum(1 for i in items if i.get("decision"))
    submitted = {r["week"] for r in records}
    streak = 0
    for w in sorted(all_weeks, reverse=True):
        if w in submitted:
            streak += 1
        else:
            break

    if items:
        cards = "".join(render_card(i) for i in items)
    elif latest.get("format") == "text" and latest.get("text"):
        li = "".join(f"<li><span>{e(t)}</span></li>" for t in latest["text"][:120])
        cards = (f'<div class="card wide"><div class="card-head"><div style="flex:1 1 auto">'
                 f'<div class="id">PDF 원문</div><h4>{e(latest["source_file"])}</h4>'
                 f'<div class="meta">PDF 보고서는 표 구조를 복원할 수 없어 본문만 옮겼습니다. '
                 f'PPTX로 제출하면 다른 부서처럼 카드로 정리됩니다.</div></div>'
                 f'<span class="chip">텍스트</span></div>'
                 f'<div class="block"><ul class="items">{li}</ul></div></div>')
    else:
        cards = ('<div class="card wide"><div class="note">이 주차 보고서에서 내용을 찾지 못했습니다. '
                 '원본 첨부가 없거나 본문이 페이지에 직접 작성됐을 수 있습니다.</div></div>')

    # 과거 주차는 표로 펼치지 않는다. 원본 링크만 남긴다.
    past_links = "".join(
        f'<a href="{e(r["url"])}" style="display:inline-block;text-decoration:none;'
        f'background:var(--surface-2);border:1px solid var(--border);border-radius:var(--radius-pill);'
        f'padding:7px 14px;margin:0 7px 8px 0;font:700 12px var(--font-secondary);'
        f'color:var(--text-secondary)">{r["week"]} ↗</a>'
        for r in records[1:])

    spark = "".join(
        f'<i class="{"" if w in submitted else "zero"}" style="height:{(80 if w in submitted else 12)}%"></i>'
        for w in all_weeks)

    body = f"""
  <div class="hero">
    <h2>{e(org)}</h2>
    <p>최신 보고 <b>{latest['week']}</b> · 항목 {len(items)}건 ·
       원본 <a href="{e(latest['url'])}" style="color:var(--accent)">컨플루언스 ↗</a>
       {f"({e(latest.get('source_file'))})" if latest.get('source_file') else ""}</p>
    <div class="kpis">
      <div class="kpi"><div class="v">{len(items)}</div><div class="l">이번 주 보고 항목</div></div>
      <div class="kpi {'warn' if n_dec else ''}"><div class="v">{n_dec}</div><div class="l">의사결정 필요</div></div>
      <div class="kpi {'warn' if n_issue else ''}"><div class="v">{n_issue}</div><div class="l">이슈 보유 항목</div></div>
      <div class="kpi"><div class="v">{streak}</div><div class="l">연속 제출 주차 (최근 {len(all_weeks)}주 중)</div></div>
    </div>
    <div class="spark">{spark}</div>
  </div>

  <section id="now">
    <div class="sec-head"><span class="sec-num">01</span>
      <h3>이번 주 — 진행사항 / 이슈사항</h3>
      <span class="desc">{latest['week']} 보고 기준</span></div>
    <div class="cards">{cards}</div>
  </section>

  <section id="hist">
    <div class="sec-head"><span class="sec-num">02</span>
      <h3>과거 주간보고 원본</h3>
      <span class="desc">컨플루언스 원본으로 이동 · {len(records) - 1}주차</span></div>
    <div>{past_links or '<span class="pending">이전 주차 기록이 없습니다.</span>'}</div>
    <div class="note">대시보드에는 <b>이번 주 보고</b>만 싣습니다.
      과거 이력은 컨플루언스 원본을 정본으로 두고 여기서는 링크만 답니다.</div>
  </section>
"""
    tabs = ('<a href="index.html">← 전체</a><a href="#now" class="on">이번 주</a>'
            '<a href="#hist">과거 원본</a>')
    stamp = (f"<b>{latest['week']}</b> 보고 기준<br>생성 {dt.datetime.now():%Y-%m-%d %H:%M}")
    return ds.page(f"{org} · 주간 운영 대시보드", f"{org} · 주간 운영 대시보드",
                   stamp, tabs, body,
                   "컨플루언스 주간업무보고 자동 수집·파싱 — 원본이 갱신되면 이 화면도 함께 갱신됩니다. "
                   "전주=P / 금주=F 로 표시하며, 이슈·의사결정 항목만 회의에서 논의합니다.")


def render_index(by_org: dict, all_weeks: list[str]) -> str:
    latest_week = all_weeks[-1] if all_weeks else "-"
    cards = []
    for org in sorted(by_org):
        recs = sorted(by_org[org], key=lambda r: r["week"], reverse=True)
        weeks = {r["week"] for r in recs}
        items = rows_to_items(recs[0])
        n_dec = sum(1 for i in items if i.get("decision"))
        n_issue = sum(1 for i in items if i.get("issue"))
        missing = latest_week not in weeks
        spark = "".join(
            f'<i class="{"" if w in weeks else "zero"}" style="height:{(80 if w in weeks else 12)}%"></i>'
            for w in all_weeks)
        href = EXTERNAL.get(org, f"{slug(org)}.html")
        badge = ('<div class="l" style="color:var(--accent)">운영 OS 대시보드 ↗</div>'
                 if org in EXTERNAL else "")
        cards.append(f"""<a class="org {'missing' if missing else ''}" href="{href}">
  <h4>{e(org)}</h4>
  <div class="l">최신 {recs[0]['week']}{' · <b style="color:var(--warn)">이번 주 미제출</b>' if missing else ''}</div>
  <div class="n">{len(items)}<span style="font-size:13px;color:var(--text-tertiary)"> 항목</span></div>
  <div class="l">결정 {n_dec} · 이슈 {n_issue}</div>{badge}
  <div class="spark">{spark}</div>
</a>""")

    total_dec = sum(sum(1 for i in rows_to_items(sorted(v, key=lambda r: r['week'])[-1])
                        if i.get("decision")) for v in by_org.values())
    missing_orgs = [o for o, v in by_org.items()
                    if latest_week not in {r["week"] for r in v}]

    body = f"""
  <div class="hero">
    <h2>전사 주간 운영 대시보드</h2>
    <p>각 부서의 컨플루언스 주간업무보고를 자동 수집해 같은 구조로 정리했습니다.
       회의에서는 <b>이슈·의사결정 항목만</b> 논의하고, 진행사항은 각자 화면에서 확인합니다.</p>
    <div class="kpis">
      <div class="kpi"><div class="v">{len(by_org)}</div><div class="l">수집 중인 부서</div></div>
      <div class="kpi"><div class="v">{len(all_weeks)}</div><div class="l">수집 주차</div></div>
      <div class="kpi {'warn' if total_dec else ''}"><div class="v">{total_dec}</div><div class="l">전사 의사결정 대기</div></div>
      <div class="kpi {'warn' if missing_orgs else ''}"><div class="v">{len(missing_orgs)}</div><div class="l">{latest_week} 미제출 부서</div></div>
    </div>
  </div>

  <section id="orgs">
    <div class="sec-head"><span class="sec-num">01</span><h3>부서별 대시보드</h3>
      <span class="desc">카드를 누르면 상세로 이동합니다</span></div>
    <div class="orgs">{"".join(cards)}</div>
    {f'<div class="note"><b>{latest_week} 미제출</b>: {", ".join(missing_orgs)}</div>' if missing_orgs else ''}
  </section>
"""
    stamp = f"기준 주차 <b>{latest_week}</b><br>생성 {dt.datetime.now():%Y-%m-%d %H:%M}"
    return ds.page("전사 주간 운영 대시보드", "전사 주간 운영 대시보드", stamp,
                   '<a href="#orgs" class="on">부서별</a>', body,
                   "컨플루언스 주간업무보고 자동 수집 — 매주 화요일 09:30 갱신. "
                   "연구기획사업부는 메일·메신저·드라이브까지 연동한 상세 대시보드를 별도로 운영합니다.")


def main() -> int:
    if not INDEX.exists():
        print("weekly_index.json 이 없습니다. collect_weekly.py 를 먼저 실행하십시오.")
        return 2
    OUT.mkdir(parents=True, exist_ok=True)

    by_org: dict[str, list[dict]] = defaultdict(list)
    for pid in json.loads(INDEX.read_text(encoding="utf-8")):
        f = PARSED / f"{pid}.json"
        if not f.exists():
            continue
        rec = json.loads(f.read_text(encoding="utf-8"))
        if rec.get("format") == "index":   # 「전사」는 부서 링크 색인일 뿐
            continue
        by_org[rec["org"]].append(rec)

    if not by_org:
        print("파싱된 보고서가 없습니다.")
        return 2

    all_weeks = sorted({r["week"] for v in by_org.values() for r in v})
    for org, recs in by_org.items():
        if org in EXTERNAL:
            stale = OUT / f"{slug(org)}.html"
            if stale.exists():
                stale.unlink()
            print(f"· {org} → 운영 OS 대시보드로 연결 (별도 생성 안 함)")
            continue
        (OUT / f"{slug(org)}.html").write_text(
            render_org(org, recs, all_weeks), encoding="utf-8")
        print(f"· {org}.html ({len(recs)}주차)")
    (OUT / "index.html").write_text(render_index(by_org, all_weeks), encoding="utf-8")
    print(f"완료 · 부서 {len(by_org)}개 · 주차 {len(all_weeks)}개 → {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
