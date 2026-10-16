#!/usr/bin/env python3
"""운영 OS 대시보드 본문의 **진행사항·이슈사항만** 원장에서 갈아끼운다 (P3).

    python3 scripts/tasks/inject.py [--dry]

왜 이렇게 하나 — 본문을 통째로 생성하면 사람이 써 둔 것들을 잃는다.
과제 카드의 부처·전담기관·참여기관·마감 칩, §04 회계, §05 RA, §06 주간보고 표는
모두 손으로 정리한 것이고 어느 스트림에도 없다.

그래서 **카드 껍데기는 그대로 두고 항목 목록만 교체한다.** 매주 바뀌는 것은 항목이지
과제의 소속 부처가 아니다.

첫 실행 때 원본을 `output/_backup/` 에 남긴다. 되돌릴 수 없는 변경은 하지 않는다.
"""
from __future__ import annotations

import datetime as dt
import html
import json
import re
import shutil
from pathlib import Path

import sys as _sys
_sys.path.insert(0, str(Path(__file__).resolve().parent))
import weekly_src as _W  # noqa: E402  — 주간보고 주차 판정은 한 곳에서만

PROJ = Path(__file__).resolve().parents[2]
REPO = PROJ.parents[1]
WEEKLY = PROJ.parents[1] / "projects" / "urban-weekly-dashboard" / "data" / "parsed"
BUDGET = PROJ.parents[1] / "projects" / "rnd-budget-agent" / "data" / "dataset"
DASH = PROJ / "output" / "rpbd_운영OS_대시보드.html"
LEDGER = PROJ / "data" / "tasks" / "ledger.jsonl"
BACKUP = PROJ / "output" / "_backup"

# 자동 교체 대상은 과제 카드가 있는 세 절뿐이다.
# §04 회계·§05 RA·§06 주간보고 원본은 별도 파싱이 필요해 아직 사람 몫이다.
AUTO_SECTIONS = ("s1", "s2", "s3")
KEEP_BACKUPS = 12

# ledger.py 의 EXCLUDE 와 같은 기준. 원장에서 빠져도 카드에 손으로 쓴 줄이 남는다.
EXCLUDE_LI = re.compile(
    r"<li>(?:(?!</li>).)*?(?:급여명세|이체확인증|통장\s*사본|계좌\s*사본|민감\s*자료|"
    r"보안\s*폴더|비밀번호|패스워드|로그인\s*정보)(?:(?!</li>).)*?</li>", re.S)


def esc(s) -> str:
    return html.escape(str(s or ""))


def read_ledger() -> list[dict]:
    if not LEDGER.exists():
        return []
    return [json.loads(x) for x in LEDGER.read_text(encoding="utf-8").splitlines()
            if x.strip()]


def when_of(t: dict) -> str:
    """화면에 붙일 날짜. **기록한 날은 쓰지 않는다.**

    `source.when` 까지 끌어다 쓰면 오늘 정리한 항목마다 오늘 날짜가 마감처럼 붙는다.
    보여줄 날짜는 실제로 끝난 날이나 기한뿐이다.
    """
    return str(t.get("done_at") or t.get("due") or "")[:10]


def item_html(t: dict, today: dt.date) -> str:
    """원본 카드와 같은 모양으로. 태그·날짜·출처 표기를 그대로 따른다."""
    # 날짜 표기는 **확실할 때만** 단다.
    #  · `09/02 또는 09/09` 처럼 확정되지 않은 문자열을 잘라 쓰면 「또는 0」 같은 게 나온다
    #  · 원문(title)에 이미 날짜가 들어 있으면 두 번 찍힌다 — 원문을 그대로 두는 대신 라벨을 뺀다
    w = when_of(t)
    label = ""
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", w):
        short = w[5:].replace("-", "/")
        if short not in t["title"][:12]:
            label = f'<span class="when">{esc(short)}</span> '
    if t.get("needs_check"):
        tag = '<span class="tag d">확인</span>'
    elif t["state"] == "done":
        tag = '<span class="tag p">P</span>'
    elif t["state"] == "blocked":
        needs = (t.get("issue") or {}).get("label") or "대기"
        cls = "c" if "대표님" in needs else ("x" if "관계자" in needs else "d")
        tag = f'<span class="tag {cls}">{esc(needs)}</span>'
    else:
        tag = '<span class="tag f">F</span>'

    src = (t.get("source") or {}).get("stream")
    mark = {"drive": "산출물", "messenger": "부서방", "mail": "메일",
            "action_ledger": "회의록", "weekly_report": "주간보고",
            "calendar": "캘린더"}.get(src)
    who = t.get("owner")
    tail = ""
    if mark or who:
        tail = f'<span class="src">{esc(" · ".join(x for x in (who, mark) if x))}</span>'
    note = f' <b style="color:var(--bad,#b42318)">{esc(t["needs_check"])}</b>' \
        if t.get("needs_check") else ""
    title = re.sub(r"\s*—\s*", ": ", t["title"])   # 문체 규칙: em dash 금지
    return f'<li>{tag}<span>{label}{esc(title)}{note} {tail}</span></li>'


# 카드를 잘라내지 않는다. `<div>` 깊이를 문자로 세어 카드 끝을 찾는 방식은 실행할 때마다
# 경계가 한 칸씩 어긋나 태그 한가운데를 잘랐다(실측: 매 실행 837B 증식·HTML 손상).
#
# 대신 **블록만** 문서 순서대로 찾고, 그 블록이 어느 과제 것인지는 **바로 앞의 `.id` 표시**로
# 정한다. 자르는 곳이 `<ul class="items">` 안쪽 하나뿐이라 구조를 깨뜨릴 수 없다.
BLOCK_RE = re.compile(
    r'<div class="block-t([^"]*)">.*?</div>\s*<ul class="items">(.*?)</ul>', re.S)
ID_RE = re.compile(r'<div class="id">(.*?)</div>', re.S)


def owner_pid(text: str, upto: int) -> str | None:
    """`upto` 앞쪽에서 가장 가까운 과제 표시를 찾는다."""
    last = None
    for m in ID_RE.finditer(text, 0, upto):
        last = m
    if not last:
        return None
    # 접미사(`PRJ-N012a`·`b`)까지 잡는다. 대문자만 받으면 창업사업화와 해외마케팅이
    # 똑같이 `PRJ-N012` 로 접혀 **두 카드에 같은 목록이 뿌려진다** — 실제로 그랬다.
    hit = re.search(r"(PRJ-[A-Z0-9]+[a-z]?)", re.sub(r"<[^>]+>", "", last.group(1)))
    return hit.group(1) if hit else None


KIND = {"매출": "sales", "R&D": "rnd", "AX": "ax", "RA": "ra"}


def latest_weekly() -> dict | None:
    """가장 최근 **제출본** 파싱본. 주차는 첨부 파일명이 정본이다 (weekly_src)."""
    return _W.latest_report(WEEKLY)


def weekly_table(d: dict) -> tuple[str, str]:
    """(표 HTML, 부제). 컨플루언스에 올린 **그 표 그대로** 다시 그린다.

    이 절은 부서 공식 양식이라 우리가 문장을 손대면 안 된다. 원본 칸 내용을 옮기고
    줄바꿈만 <br> 로 바꾼다.
    """
    head = d.get("header", [])
    def col(pat):
        for h in head:
            if re.search(pat, h):
                return h
        return None
    c = [col(r"^NO|번호"), col(r"구분"), col(r"PJT|과제"),
         col(r"전주|실적"), col(r"금주|계획"), col(r"이슈")]
    rows = []
    for it in d["items"]:
        cells = []
        for i, key in enumerate(c):
            v = re.sub(r"[\r\n\x0b]+", "<br>", str(it.get(key) or "").strip()) if key else ""
            v = esc(re.sub(r"<br>", "\x00", v)).replace("\x00", "<br>") or "—"
            if i == 1 and v != "—":
                cells.append(f'<td><span class="kind {KIND.get(v.strip(), "etc")}">{v}</span></td>')
            elif i == 2:
                cells.append(f"<td><b>{v}</b></td>")
            else:
                cells.append(f"<td>{v}</td>")
        rows.append("<tr>" + "".join(cells) + "</tr>")
    ths = "".join(f"<th>{esc(h).replace(' / ', '<br>')}</th>" for h in
                  [x for x in c if x] or head)
    tbl = (f'<table><thead><tr>{ths}</tr></thead>'
           f'<tbody>{"".join(rows)}</tbody></table>')
    src = d.get("source_file") or d.get("week")
    day = d.get("_보고일") or _W.report_date(d)
    age = ""
    if day:
        try:
            n = (dt.date.today() - dt.date.fromisoformat(day)).days
            age = f" · 보고일 {day}" + (f" ({n}일 전)" if n >= 7 else "")
        except ValueError:
            age = ""
    return tbl, f"부서 공식 양식 그대로 · {esc(src)}{esc(age)}"


def _core(s: str) -> str:
    """비교용 골자 — 「(기관) 과제명 — 」 접두와 기호를 걷어낸다."""
    s = re.sub(r"^\([^)]*\)[^—]*—\s*", "", s)
    return re.sub(r"[^0-9a-z가-힣]", "", s.lower())


def _sim(a: str, b: str) -> float:
    """짧은 쪽 2-gram이 긴 쪽에 얼마나 담기는가 (0~1)."""
    A = {a[i:i + 2] for i in range(len(a) - 1)}
    B = {b[i:i + 2] for i in range(len(b) - 1)}
    if not A or not B:
        return 0.0
    return len(A & B) / min(len(A), len(B))


def _same_slot(a: dict, b: dict) -> bool:
    """기한이 서로 다르면 문구가 닮아도 **다른 일**이다.

    「[AX SPRINT-복지]중간평가보고서 마감 09/16」과 「[AX SPRINT-복지] 중간평가회
    09/30」이 유사도 0.93 으로 접혀, 9월 말 마감 하나가 화면에서 사라졌다.
    """
    da, db = (a.get("due") or "")[:10], (b.get("due") or "")[:10]
    return not (da and db and da != db)


def dedupe(rows: list[dict]) -> list[dict]:
    """같은 과제 안에서 같은 사안은 **한 번만** 싣는다 (Roy 지시 2026-08-31).

    출처가 달라 문구가 조금 다른 같은 사안(손 교정 vs 주간보고)이 진행·이슈 양쪽에
    나뉘어 실리던 것이 중복의 뿌리다. 이슈 측 > 원장 신뢰도 > 긴(더 정보 많은) 문구
    순으로 남긴다.
    """
    def rank(t):
        return (0 if (t["state"] == "blocked" or t.get("needs_check")) else 1,
                0 if t.get("confidence") == "ledger" else 1,
                -len(t["title"]), t["title"])
    kept: list[tuple[str, dict]] = []
    for t in sorted(rows, key=rank):
        c = _core(t["title"])
        if len(c) >= 10 and any(_sim(c, k) >= 0.72 and _same_slot(t, kt)
                                for k, kt in kept):
            continue
        kept.append((c, t))
    return [t for _, t in kept]


def severity(t: dict) -> int:
    """0=대표 결정, 1=막힘(대기·관계자간·부서간 …), 2=확인 필요."""
    if (t.get("issue") or {}).get("needs") == "대표님컨펌":
        return 0
    if t["state"] == "blocked":
        return 1
    return 2


def card_names(doc: str) -> tuple[dict[str, tuple[str, str]], dict[str, int]]:
    """과제 이름은 따로 관리하지 않는다 — 카드에 손으로 써 둔 .id/.h4 가 정본이다."""
    names: dict[str, tuple[str, str]] = {}
    order: dict[str, int] = {}
    for i, m in enumerate(re.finditer(
            r'<div class="id">(.*?)</div>\s*<h4>(.*?)</h4>', doc, re.S)):
        hit = re.search(r"(PRJ-[A-Z0-9]+[a-z]?)", re.sub(r"<[^>]+>", "", m.group(1)))
        if hit and hit.group(1) not in names:
            names[hit.group(1)] = (m.group(1).strip(), m.group(2).strip())
            order[hit.group(1)] = i
    return names, order


def issue_brief_html(tasks: list[dict], doc: str, today: dt.date) -> str:
    """§00 — 과제별 이슈 브리핑. 결정·공유가 필요한 것만, 심각한 과제부터.

    보고의 우선순위: 이슈가 먼저다. 진행사항은 읽고 넘어가는 것이고,
    공유해야 할 것은 이슈다 (Roy 지시 2026-08-31). 그래서 이 절이 맨 위다.
    """
    names, order = card_names(doc)

    groups: dict[str, list[dict]] = {}
    for t in tasks:
        if t["state"] == "blocked" or t.get("needs_check"):
            groups.setdefault(t.get("project_id") or "_공통", []).append(t)

    def group_key(pid: str):
        worst = min(severity(t) for t in groups[pid])
        return (worst, pid == "_공통", order.get(pid, 98), pid)

    cards = []
    for pid in sorted(groups, key=group_key):
        rows = sorted(groups[pid], key=lambda t: (severity(t), t["title"]))
        dec = sum(1 for t in rows if severity(t) == 0)
        if pid == "_공통":
            idline, title = "공통", "RA 인허가 · 회계 · 부서 운영"
        else:
            idline, title = names.get(pid, (esc(pid), "카드 미등록: 태스크 상세 참조"))
        chip = (f'<span class="chip hot">결정 {dec}</span>' if dec
                else f'<span class="chip">공유 {len(rows)}</span>')
        body = "".join(item_html(t, today) for t in rows)
        cards.append(
            f'<div class="card"><div class="card-head">'
            f'<div style="flex:1 1 auto"><div class="id">{idline}</div>'
            f'<h4>{title}</h4></div>{chip}</div>'
            f'<div class="block"><ul class="items">{body}</ul></div></div>')
    if not cards:
        cards = ['<div class="card"><div class="block"><ul class="items">'
                 '<li><span class="tag d">-</span><span>결정·공유할 이슈 없음</span></li>'
                 '</ul></div></div>']
    return "".join(cards) + questions_card(today)


def questions_card(today: dt.date) -> str:
    """§00 끝에 붙는 「성장 질문」 — 기계가 확신 못 한 맥락을 Roy에게 묻는다.

    답이 규칙이 되어 다음 사이클부터 자동 처리된다 (Roy 지시 2026-08-31:
    "애매한 맥락이 나오면 질문해서 지식체계를 계속 성장"). 버리는 대신 묻는다.
    """
    qf = PROJ / "data" / "tasks" / "questions.jsonl"
    if not qf.exists():
        return ""
    rows = [json.loads(x) for x in qf.read_text(encoding="utf-8").splitlines()
            if x.strip()]
    open_qs = sorted((q for q in rows if q.get("status") == "open"),
                     key=lambda q: (q.get("day") or "", q["qid"]), reverse=True)
    if not open_qs:
        return ""
    lis = []
    for q in open_qs[:5]:
        day = (q.get("day") or "")[5:].replace("-", "/")
        lis.append(
            f'<li><span class="tag d">Q</span><span>'
            f'{f"<span class={chr(34)}when{chr(34)}>{esc(day)}</span> " if day else ""}'
            f'{esc(re.sub(r'\s*—\s*', ': ', q["text"][:110]))} <b>→ {esc(q["ask"])}</b> '
            f'<span class="src">{esc(q["source"])} · {esc(q["qid"])}</span></span></li>')
    more = f' 외 {len(open_qs) - 5}건' if len(open_qs) > 5 else ""
    return (f'<div class="card wide"><div class="card-head">'
            f'<div style="flex:1 1 auto"><div class="id">성장 질문</div>'
            f'<h4>애매한 맥락 {len(open_qs)}건{more}: 답하면 규칙이 됩니다</h4>'
            f'<div class="meta">답변: claudex 세션 또는 부서방에서 · 기록은 learn.py</div></div>'
            f'<span class="chip">Q {len(open_qs)}</span></div>'
            f'<div class="block"><ul class="items">{"".join(lis)}</ul></div></div>')


def _won(v) -> str:
    return f"{round(v):,}" if isinstance(v, (int, float)) else "-"


def acct_html(tasks: list[dict], names: dict[str, tuple[str, str]],
              order: dict[str, int]) -> str:
    """§04 — {{MEMBER_A}} 예산 원장(엑셀→데이터셋)에서 편성·집행·잔액을 그대로 옮긴다.

    계산 안 된 값(#REF!·빈 시트)은 숫자를 지어내지 않고 — 로 둔다. 요약이 깨진
    시트는 해석된 라인 합계로 대신하되 「라인 합」이라고 밝힌다.
    """
    files = sorted(BUDGET.glob("budget_*.json"))
    if not files:
        return '<p class="src">예산 데이터셋 없음 — rnd-budget-agent 미가동</p>'
    d = json.loads(files[-1].read_text(encoding="utf-8"))

    rows = []
    for p in sorted(d.get("projects", []),
                    key=lambda p: (order.get(p["project_id"], 98), p["project_id"])):
        sm = p.get("summary") or {}
        def tot(k):
            v = (sm.get(k) or {}).get("총계")
            return v if isinstance(v, (int, float)) else None
        def lsum(k):
            vs = [ln.get(k) for ln in (p.get("lines") or [])
                  if isinstance(ln.get(k), (int, float))]
            return sum(vs) if vs else None
        # 한 행에 요약값과 라인 합을 섞지 않는다 — 기준이 다른 숫자를 나란히 두면
        # 「잔액 > 편성」 같은 거짓 그림이 나온다 (260825 실측). 행 단위 단일 기준.
        plan, spent, left = tot("예산"), tot("사용"), tot("차액")
        if plan is not None and spent is not None:
            basis = "요약 시트"
        else:
            plan, spent, left = lsum("편성_계"), lsum("집행"), lsum("잔액")
            cov = p.get("coverage") or {}
            basis = f'해석 라인 {cov.get("lines", "?")}개 합 · 부분값' \
                if plan is not None or spent is not None else None
        warn = "; ".join(p.get("warnings") or [])
        status = (basis or "") + \
                 (f' <b style="color:var(--warn,#b54708)">{esc(re.sub(r"\s*—\s*", ": ", warn))}</b>' if warn else "")
        name = names.get(p["project_id"], (None, None))[1] or esc(p.get("sheet", p["project_id"]))
        rows.append(f'<tr><td><b>{name}</b></td><td class="num">{_won(plan)}</td>'
                    f'<td class="num">{_won(spent)}</td><td class="num">{_won(left)}</td>'
                    f'<td>{status or "-"}</td></tr>')
    tbl = ('<div class="tablewrap"><table><thead><tr><th>과제</th><th>편성 총계</th>'
           '<th>집행</th><th>잔액</th><th>상태</th></tr></thead>'
           f'<tbody>{"".join(rows)}</tbody></table></div>')

    # 최근 판(版) 사이에서 실제로 움직인 돈 — 이것이 「결과」다.
    changes = ""
    hist = BUDGET / "history.jsonl"
    if hist.exists():
        hl = [json.loads(x) for x in hist.read_text(encoding="utf-8").splitlines() if x.strip()]
        if hl:
            h = hl[-1]
            moves = sorted((e for e in h["entries"] if e.get("종류") == "집행 변화"
                            and isinstance(e.get("증감"), (int, float))),
                           key=lambda e: -abs(e["증감"]))[:5]
            lis = "".join(
                f'<li><span class="tag p">P</span><span><b>{esc(e["과제"])}</b> '
                f'{esc(e["라인"].split("/")[-1].strip())} {_won(e["증감"])}</span></li>'
                for e in moves)
            changes = (f'<div class="block-t"><span class="dot"></span>원장 {h["from"]} >> '
                       f'{h["to"]} 변경 {h["n"]}건: 집행 변화 상위</div>'
                       f'<ul class="items">{lis}</ul>')

    # 메신저·메일·주간보고에서 관측된 회계 결과 — 완료건은 §01에서 뺐으므로 여기가 유일한 자리다.
    KW = re.compile(r"지출결의|부가세|정산|증빙|세금계산서|이체|입금|인건비|회계")
    acct_done = sorted((t for t in tasks if t["state"] == "done" and KW.search(t["title"])),
                       key=lambda t: str(t.get("done_at") or ""), reverse=True)[:8]
    observed = ""
    if acct_done:
        today = dt.date.today()
        observed = ('<div class="block-t"><span class="dot"></span>관측된 회계 결과 '
                    '(메신저·메일·주간보고)</div><ul class="items">'
                    + "".join(item_html(t, today) for t in acct_done) + "</ul>")

    src = esc(d.get("source_file", ""))
    foot = (f'<div class="src" style="margin-top:8px">기준 {esc(d.get("as_of", "?"))} · {src} · '
            f'깨진 수식(#REF!)·빈 시트는 - 로 표기 · 월 인건비 표는 인건비 파서 이후</div>')
    return tbl + changes + observed + foot


def weekly_draft_html() -> str:
    """§07 — 차주 주간보고 초안. 리더가 여기서 시작하도록 (Roy 지시 2026-08-31).

    공식 양식과 같은 칸이되, 원장이 채운 것과 이월(사람 확인 필요)을 구분해 보인다.
    확정 pptx 초안은 make_weekly_pptx.py 가 같은 데이터로 만든다.
    """
    f = PROJ / "data" / "tasks" / "weekly_draft.json"
    if not f.exists():
        return '<p class="src">초안 데이터 없음</p>'
    d = json.loads(f.read_text(encoding="utf-8"))
    rows = []
    for r in d["rows"]:
        def cell(s):
            return esc(s).replace("\n", "<br>") or "-"
        prev = cell(r["전주"])
        if True:
            prev = prev.replace("(지속)", '<b style="color:var(--warn,#b54708)">(지속)</b>')
        kind = r["구분"]
        kd = f'<span class="kind {KIND.get(kind, "etc")}">{esc(kind)}</span>' if kind else "-"
        rows.append(f'<tr><td>{esc(r["no"]) or "-"}</td><td>{kd}</td>'
                    f'<td><b>{esc(r["pjt"])}</b></td><td>{prev}</td>'
                    f'<td>{cell(r["금주"])}</td><td>{cell(r["이슈"])}</td></tr>')
    auto = sum(1 for r in d["rows"] if r["전주_출처"] == "증거")
    carry = sum(1 for r in d["rows"] if "(지속)" in r["금주"])
    rd = d["report_date"]
    return (
        f'<div class="tablewrap"><table><thead><tr><th>NO</th><th>구분</th><th>PJT</th>'
        f'<th>전주 실적<br>({esc(d["week_from"][5:])}~{esc(d["week_to"][5:])})</th>'
        f'<th>금주 계획</th><th>이슈</th></tr></thead>'
        f'<tbody>{"".join(rows)}</tbody></table></div>'
        f'<div class="src" style="margin-top:8px">{esc(rd)}(화) 보고용 초안 · '
        f'실적 증거 {auto}행 · 지속 {carry}행(주황 (지속) 은 사람 확인 필요) · '
        f'pptx 초안: SSOT 00_부서정본/주간보고_초안/ · 원장 갱신마다 자동 재생성</div>')


# ---------------------------------------------------------------- 신선도 스트립
#
# 배지 하나로는 거짓말을 한다. 매 사이클 `data-asof` 에 지금 시각을 찍으니 화면은
# 언제나 「본문 최신」이고, 그 초록 배지 아래에 8/13 에 끝난 인허가와 8/4 타 부서
# 문장이 나란히 놓인다. Roy 정정 3(2026-08-19)의 취지는 **본문 나이와 수집 나이를
# 가르는 것**이었는데 구현이 그것을 되돌려 놓았다.
#
# 그래서 **스트림마다 언제까지 반영됐는지**를 따로 적는다. 사람에게 새로 입력받는
# 값은 하나도 없다 — 전부 파일 시각과 원장·데이터셋 값에서 나온다.


def _newest(paths) -> dt.date | None:
    best = None
    for q in paths:
        try:
            d = dt.date.fromtimestamp(q.stat().st_mtime)
        except OSError:
            continue                       # 클라우드 마운트는 간헐적으로 잠긴다
        if best is None or d > best:
            best = d
    return best


def _as_day(v) -> str | None:
    """'260825'·'2026-08-25'·'2026-08-25T...' → 'YYYY-MM-DD'."""
    v = str(v or "")
    if re.fullmatch(r"\d{6}", v):
        return f"20{v[:2]}-{v[2:4]}-{v[4:]}"
    m = re.search(r"20\d{2}-\d{2}-\d{2}", v)
    return m.group(0) if m else None


def freshness_rows() -> list[tuple[str, str | None, str]]:
    """(스트림, 기준일, 덧말)."""
    nw = REPO / "projects" / "nworks-agent" / "output"
    iso = lambda d: d.isoformat() if d else None          # noqa: E731

    obs, budget = PROJ / "data" / "tasks" / "candidates_observed.jsonl", None
    drive_day = None
    if obs.exists():
        for line in obs.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            t = json.loads(line)
            if (t.get("source") or {}).get("stream") != "drive":
                continue
            w = _as_day((t.get("source") or {}).get("when"))
            if w and (drive_day is None or w > drive_day):
                drive_day = w
    files = sorted(BUDGET.glob("budget_*.json")) if BUDGET.is_dir() else []
    if files:
        try:
            budget = _as_day(json.loads(files[-1].read_text(encoding="utf-8")).get("as_of"))
        except (ValueError, OSError):
            budget = None

    ufeed = iso(_newest([REPO / "state" / "U_변경_최근.md"]))
    w = latest_weekly() or {}
    return [
        ("메일", iso(_newest([nw / "raw_mail" / "{{BOT_USER}}" / "state.json"])), ""),
        ("부서방", iso(_newest(nw.glob("raw_messenger/*/*.jsonl"))), ""),
        ("캘린더", iso(_newest(nw.glob("raw_calendar/*/*.json"))), ""),
        ("주간보고", w.get("_보고일") or None, ""),
        ("회의록 액션",
         iso(_newest([REPO / "projects" / "fathom-agent" / "data" / "actions.json"])), ""),
        ("SSOT 산출물", drive_day, ""),
        ("예산 원장", budget, ""),
        ("U 드라이브", ufeed, "원장 미연결"),
    ]


def freshness_html() -> str:
    today = dt.date.today()
    pills, oldest = [], None
    for name, day, note in freshness_rows():
        age = None
        if day:
            try:
                age = (today - dt.date.fromisoformat(day)).days
            except ValueError:
                age = None
        color = ("var(--muted,#667085)" if age is None
                 else "var(--ok,#1a7f37)" if age <= 1
                 else "var(--warn,#b54708)" if age <= 6
                 else "var(--bad,#b42318)")
        if age is not None and (oldest is None or age > oldest[1]):
            oldest = (name, age, day)
        txt = f"{name} {day[5:] if day else '미상'}"
        if age is not None and age >= 2:
            txt += f" · {age}일"
        if note:
            txt += f" · {note}"
        pills.append(
            f'<span style="display:inline-block;padding:2px 8px;border-radius:99px;'
            f'border:1px solid currentColor;font-size:11px;margin:0 4px 4px 0;'
            f'color:{color}">{esc(txt)}</span>')
    tail = ""
    if oldest and oldest[1] >= 7:
        tail = (f'<div style="font-size:11px;opacity:.75;margin-top:2px">'
                f'가장 낡은 축: {esc(oldest[0])} {esc(oldest[2])} '
                f'({oldest[1]}일 전) · 이 절의 내용은 그 시점까지만 반영돼 있습니다</div>')
    return ('<div style="margin:6px 0 2px">' + "".join(pills) + "</div>" + tail)


def main(dry: bool = False) -> int:
    if not DASH.exists():
        print(f"대시보드 없음: {DASH}")
        return 2
    tasks = read_ledger()
    if not tasks:
        print("원장이 비어 있다 — 본문을 건드리지 않는다")
        return 1

    # 같은 사안은 과제당 한 번만 — 화면에 나가기 전에 접는다 (원장은 손대지 않는다).
    _groups: dict[str, list] = {}
    for t in tasks:
        _groups.setdefault(t.get("project_id") or "", []).append(t)
    before = len(tasks)
    tasks = [t for g in _groups.values() for t in dedupe(g)]
    if len(tasks) < before:
        print(f"· 중복 접힘 {before - len(tasks)}건 (화면 기준)")

    today = dt.date.today()
    by_pid: dict[str, list] = {}
    for t in tasks:
        if t.get("project_id"):
            by_pid.setdefault(t["project_id"], []).append(t)

    s = DASH.read_text(encoding="utf-8")
    if not dry:
        BACKUP.mkdir(parents=True, exist_ok=True)
        shutil.copy2(DASH, BACKUP / f"rpbd_운영OS_대시보드_{dt.datetime.now():%Y%m%dT%H%M}.html")
        # 시간당으로 돌면 하루 24개가 쌓인다. 되돌리기용이므로 최근 것만 있으면 된다.
        old = sorted(BACKUP.glob("rpbd_운영OS_대시보드_*.html"))[:-KEEP_BACKUPS]
        for f in old:
            f.unlink()

    # 대상 구간 = s1 시작 ~ s4 시작
    try:
        beg = s.index('id="s1"')
        fin = s.index('id="s4"')
    except ValueError:
        print("절 앵커(s1·s4)를 찾지 못했다 — 중단")
        return 1

    replaced = 0
    seen_pids = set()
    for bm in reversed(list(BLOCK_RE.finditer(s, beg, fin))):
        pid = owner_pid(s, bm.start())
        rows = by_pid.get(pid or "", [])
        if not rows:
            continue                      # 과제를 못 찾으면 **그 블록은 그대로 둔다**
        seen_pids.add(pid)

        is_issue = "issue" in bm.group(1)
        if is_issue:
            rowset = [t for t in rows if t["state"] == "blocked" or t.get("needs_check")]
            rowset.sort(key=lambda t: ((t.get("issue") or {}).get("needs") != "대표님컨펌",))
        else:
            # 이슈(막힘·확인 필요)는 §00 브리핑에만 싣는다. 진행 목록에 겹쳐 실으면
            # 같은 줄이 두 번 보이고, 구분 자체가 무의미해진다 (Roy 지시 2026-08-31).
            # 완료건은 나열하지 않는다 — 건수만 남기고 상세·주간 정산은 원장이 맡는다.
            rowset = [t for t in rows if t["state"] in ("planned", "doing")
                      and not t.get("needs_check")]
            rowset.sort(key=lambda t: (when_of(t), t["title"]))

        body = "".join(item_html(t, today) for t in rowset)
        if not is_issue:
            done_n = sum(1 for t in rows if t["state"] == "done")
            if done_n:
                body += (f'<li><span class="tag p">P</span><span>완료 {done_n}건: '
                         f'나열 생략, 화요일 정산 후 아카이브 <span class="src">태스크 상세'
                         f'</span></span></li>')
        body = body or '<li><span class="tag d">-</span><span>이번 주 해당 없음</span></li>'
        s = s[:bm.start(2)] + body + s[bm.end(2):]
        replaced += 1
    touched = len(seen_pids)

    # ---- §00 이슈 브리핑 — 컨테이너 안쪽만 갈아끼운다. 끝 경계는 다음 절 앵커로 잡는다
    # (HTML 주석은 아래 정리 단계가 지우므로 표식으로 쓸 수 없다 — 컨플루언스에서 배운 것).
    bm = re.search(r'(<div class="cards" id="issue-brief">).*?'
                   r'(</div>\s*</section>\s*<section id="s1")', s, re.S)
    if bm:
        s = s[:bm.end(1)] + issue_brief_html(tasks, s, today) + s[bm.start(2):]
    else:
        print("  경고: §00 이슈 브리핑 컨테이너 없음 — 건너뜀")

    # ---- §04 회계 — {{MEMBER_A}} 예산 원장 데이터셋에서 표를 다시 그린다.
    # 끝 표식은 빈 div — 주석은 정리 단계에서 지워지므로 못 쓴다.
    am = re.search(r'(<div id="acct-auto">).*?(<div id="acct-auto-end">)', s, re.S)
    if am:
        names, order = card_names(s)
        s = s[:am.end(1)] + acct_html(tasks, names, order) + s[am.start(2):]
    else:
        print("  경고: §04 자동표 컨테이너 없음 — 건너뜀")

    # ---- §07 주간보고 초안 — 리더의 화요일이 빈 표에서 시작하지 않도록.
    wm = re.search(r'(<div id="weekly-draft">).*?(<div id="weekly-draft-end">)', s, re.S)
    if wm:
        s = s[:wm.end(1)] + weekly_draft_html() + s[wm.start(2):]

    # ---- 신선도 스트립 — 절마다 언제까지 반영됐는지
    fm = re.search(r'(<div id="fresh-streams">).*?(<div id="fresh-streams-end">)', s, re.S)
    if fm:
        s = s[:fm.end(1)] + freshness_html() + s[fm.start(2):]
    else:
        print("  경고: 신선도 스트립 컨테이너 없음 — 건너뜀")

    # ---- 머리말 갱신
    #
    # 배지만 고치고 「최종 갱신 …」 글자를 두면 화면은 여전히 지난주를 말한다.
    # 사람은 배지가 아니라 **문장을 읽는다.**
    now = dt.datetime.now()
    stamp = f"{now:%Y-%m-%dT%H:%M}"
    s = re.sub(r'(<span id="freshness" data-asof=")[^"]*(")', rf"\g<1>{stamp}\g<2>", s)

    nxt = today + dt.timedelta(days=(1 - today.weekday()) % 7)
    head = (f'<b>{nxt:%m/%d}(화)</b> 주간회의 &nbsp;·&nbsp; '
            f'최종 갱신 <b>{now:%Y-%m-%d %H:%M}</b> · 자동<br>')
    s = re.sub(r'(<span id="freshness"[^>]*></span>\s*).*?(?=\s*<a href="태스크_상세)',
               lambda m: m.group(1) + head, s, count=1, flags=re.S)

    # ---- 판단에 필요 없는 것을 걷어낸다
    #
    # 이 화면은 **무엇을 정할 것인가**를 보는 곳이다. 지난 회의 공지, 어떤 원천을 몇 건
    # 읽었는지, P/F 태그가 무슨 뜻인지 같은 설명은 화면을 채우기만 하고 판단을 돕지 않는다.
    # 한 번 지워도 다음 갱신에 낡은 채로 되살아나므로 **생성 단계에서 지운다.**
    s = re.sub(r'<div class="alert">.*?</div>\s*</div>', "</div>", s, flags=re.S)

    # 보안·개인정보 항목은 이 화면의 안건이 아니다. 과제가 붙지 않은 카드의 블록은
    # 생성기가 갈아끼우지 않으므로, **손으로 쓴 줄도 여기서 직접 걷어낸다.**
    s = EXCLUDE_LI.sub("", s)
    s = re.sub(r'(<div class="hero">\s*<h2>.*?</h2>)\s*<p>.*?</p>', r"\1", s, flags=re.S)
    s = re.sub(r'<div class="note">.*?</div>', "", s, flags=re.S)
    s = re.sub(r'<!--(?!\[if).*?-->', "", s, flags=re.S)          # 조건부 주석은 남긴다
    s = re.sub(r'\n{3,}', "\n\n", s)

    # ---- 머리 수치는 원장에서 다시 센다. 손으로 박아 두면 이번 주에도 지난주 숫자가 뜬다.
    #
    # 카드를 통째로 치환하려 했더니 `.kpi` 안에 `<div>` 가 겹쳐 있어 정규식이 첫 카드
    # 중간에서 끊겼고, 새 카드가 옛 카드 **위에 덧붙었다**. 그래서 카드를 하나씩 지운 뒤
    # 컨테이너 바로 뒤에 새로 넣는다.
    soon_n = sum(1 for t in tasks if t.get("due")
                 and today.isoformat() <= t["due"][:10]
                 <= (today + dt.timedelta(days=21)).isoformat())
    kpis = [(sum(1 for t in tasks if (t.get("issue") or {}).get("needs") == "대표님컨펌"),
             "대표님 결정 필요", True),
            (sum(1 for t in tasks if t.get("needs_check")), "확인 필요 (기한 경과·소식 없음)", True),
            (soon_n, "3주 내 마감", False),
            (len({t["project_id"] for t in tasks if t.get("project_id")}), "관리 중 과제", False)]
    cards = "".join(
        f'<div class="kpi{" warn" if w and n else ""}"><div class="v">{n}</div>'
        f'<div class="l">{esc(lab)}</div></div>' for n, lab, w in kpis)
    # 카드 하나는 <div.kpi><div.v>…</div><div.l>…</div></div> 다 — 닫는 태그는 **둘**이다.
    # 셋으로 잡으면 마지막 카드에서 kpis 컨테이너와 hero 까지 먹어 §01 이 통째로 사라진다.
    s = re.sub(r'<div class="kpi(?: warn)?"><div class="v">.*?</div>\s*'
               r'<div class="l">.*?</div>\s*</div>', "", s, flags=re.S)
    s = s.replace('<div class="kpis">', f'<div class="kpis">{cards}', 1)

    # ---- §06 주간보고표는 **최신 제출본으로 계속 갈아끼운다.**
    # 손으로 박아 두니 260811 초안이 2주 넘게 걸려 있었다.
    wk = latest_weekly()
    if wk:
        tbl, sub = weekly_table(wk)
        seg = s[s.index('id="s6"'):]
        s = s.replace(re.search(r"<table>.*?</table>", seg, re.S).group(0), tbl, 1)
        i6 = s.index('id="s6"')
        m = re.search(r'<span class="desc">.*?</span>', s[i6:], re.S)
        if m:
            s = s[:i6 + m.start()] + f'<span class="desc">{sub}</span>' + s[i6 + m.end():]

    dec, chk = kpis[0][0], kpis[1][0]
    blk = sum(1 for t in tasks if t["state"] == "blocked"
              and (t.get("issue") or {}).get("needs") != "대표님컨펌")
    s = re.sub(r'(<div class="hero">\s*<h2>).*?(</h2>)',
               rf"\g<1>결정할 것 {dec}건 · 공유할 이슈 {blk}건 · 확인할 것 {chk}건\g<2>",
               s, count=1, flags=re.S)

    if dry:
        print(f"[모의] 카드 {touched}개 · 블록 {replaced}개 교체 예정")
        return 0
    DASH.write_text(s, encoding="utf-8")
    print(f"· 본문 갱신 — 카드 {touched}개 · 블록 {replaced}개 · 기준 {stamp}")
    print(f"  백업 {BACKUP.relative_to(PROJ)}/")
    return 0


if __name__ == "__main__":
    import sys
    raise SystemExit(main("--dry" in sys.argv))
