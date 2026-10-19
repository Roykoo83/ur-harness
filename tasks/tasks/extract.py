#!/usr/bin/env python3
"""스트림에서 태스크 후보를 뽑는다 (P1 — 원장 계열만).

    python3 scripts/tasks/extract.py

P1 범위는 **ledger 계열**이다. 사람이 명시적으로 쓴 것만 읽는다 —
컨플루언스 주간보고, 회의록 액션 원장, 캘린더의 마감성 일정.
메신저·메일 같은 관측 계열은 P2 에서 붙인다. 추론이 섞이면 검증이 어려워지므로
확실한 것부터 쌓는다.

**문장을 새로 쓰지 않는다.** 원문을 그대로 title 로 쓴다.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import re
from pathlib import Path

import sys as _sys
_sys.path.insert(0, str(Path(__file__).resolve().parent))
import weekly_src as _W  # noqa: E402  — 주간보고 주차 판정은 한 곳에서만 (weekly_src.py)

PROJ = Path(__file__).resolve().parents[2]
REPO = PROJ.parents[1]
WEEKLY = _weekly_dir("parsed")
ACTIONS = REPO / "projects" / "fathom-agent" / "data" / "actions.json"
CAL = REPO / "projects" / "nworks-agent" / "output" / "raw_calendar"
LEDGER = PROJ / "data" / "records" / "project"

ORG = "연구기획사업부"
# 캘린더는 회의로 가득 차 있다. 전부 태스크로 올리면 보드가 일정표가 된다.
# **기한이 걸린 일**만 집는다 — 아래 말이 제목에 있을 때.
# 「심사」·「실사」가 없어 고영향 AI 예비심사(09-21)·본심사(10-19)가 통째로 빠졌다.
CAL_KEYWORDS = re.compile(r"마감|제출|심의|심사|실사|접수|평가|점검|보고회|발표|종료|만료|D-\d")
# 값이 없다는 뜻으로 부서가 쓰는 표현들. 태스크가 아니다.
EMPTY = re.compile(r"^\s*(?:[-–—]|없음|특이사항\s*없음|해당\s*없음|N/?A)?\s*$", re.I)



def _weekly_dir(sub: str = "") -> Path:
    """주간보고 데이터 경로 — 설정의 작업공간 아래. 코드 경로와 섞지 않는다."""
    import sys as _s
    _s.path.insert(0, str(Path(__file__).resolve().parents[2] / "core"))
    from lib.config import paths                                 # noqa: PLC0415
    return paths.weekly / sub if sub else paths.weekly


def norm(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "").replace("", " ")).strip()


# 「8월 21일(금)」 처럼 **날짜만 있는 줄**. PPTX 칸에서는 날짜와 내용을 줄 바꿔 쓰는데,
# 그대로 나누면 날짜 한 건·내용 한 건이 되어 **둘 다 뜻을 잃는다.**
DATE_ONLY = re.compile(r"^\s*(\d{1,2})\s*월\s*(\d{1,2})\s*일\s*(?:\([월화수목금토일]\))?\s*$")
CELL_SPLIT = re.compile(r"[\n\r\x0b]+")     # PPTX 는 줄바꿈에 세로탭(\x0b)도 쓴다


def cell_items(raw, year: int) -> list[tuple[str, str | None]]:
    """칸 하나 → [(문장, 날짜)]. 날짜만 있는 줄은 **다음 줄에 붙인다.**"""
    lines = [norm(x) for x in CELL_SPLIT.split(str(raw or ""))]
    lines = [x for x in lines if x and not EMPTY.match(x)]
    out, pending = [], None
    for line in lines:
        m = DATE_ONLY.match(line)
        if m:
            try:
                pending = dt.date(year, int(m.group(1)), int(m.group(2))).isoformat()
            except ValueError:
                pending = None
            continue
        out.append((line, pending))
        pending = None
    if pending and not out:        # 날짜만 있고 내용이 없으면 날짜 자체가 항목이다
        out.append((norm(raw), pending))
    return out


def _md_due(raw: str) -> str | None:
    """옛 규칙(`M/D` 만). **제목 꼬리를 붙일지 정할 때만** 쓴다 — 아래 주석 참조."""
    m = re.search(r"(\d{1,2})/(\d{1,2})", raw or "")
    if not m:
        return None
    try:
        return dt.date(dt.date.today().year, int(m.group(1)), int(m.group(2))).isoformat()
    except ValueError:
        return None


def parse_due(raw: str) -> str | None:
    """자유 문자열 기한 → 날짜. 못 읽으면 None.

    회의록 액션의 기한은 `~08/18`·`2026-09-21`·`9월 21일`·`서버 확보 후` 가 섞여 있다.
    `M/D` 만 읽던 시절엔 ISO 로 적힌 12건이 통째로 버려져, 부서에서 가장 큰 기한인
    고영향 AI 예비심사(2026-09-21)가 기한 목록·KPI 어디에도 없었다.
    """
    if not raw:
        return None
    m = re.search(r"(20\d{2})[-./](\d{1,2})[-./](\d{1,2})", raw)
    if m:
        y, mo, dd = (int(x) for x in m.groups())
    else:
        m = (re.search(r"(\d{1,2})\s*월\s*(\d{1,2})\s*일", raw)
             or re.search(r"(\d{1,2})/(\d{1,2})", raw))
        if not m:
            return None
        y, mo, dd = dt.date.today().year, int(m.group(1)), int(m.group(2))
    try:
        return dt.date(y, mo, dd).isoformat()
    except ValueError:
        return None


def task_id(project_id: str | None, title: str) -> str:
    key = f"{project_id or '-'}|{re.sub(r'[^0-9A-Za-z가-힣]', '', title)}"
    return "T-" + hashlib.sha1(key.encode()).hexdigest()[:10]


def project_index() -> list[tuple[str, list[str]]]:
    """(project_id, 매칭용 이름들). 주간보고의 PJT 칸은 과제 ID 를 쓰지 않는다."""
    files = sorted(LEDGER.glob("*.jsonl")) if LEDGER.is_dir() else []
    if not files:
        return []
    out, seen = [], set()
    for line in files[-1].read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        p = json.loads(line).get("payload", {})
        pid = p.get("project_id")
        if not pid or pid in seen:
            continue
        seen.add(pid)
        names = [n for n in (p.get("name_short"), p.get("name_official")) if n]
        out.append((pid, names))
    return out


def match_project(text: str, index) -> str | None:
    """PJT 칸 문구에서 과제를 찾는다. 못 찾으면 **None** — 추측하지 않는다."""
    t = norm(text)
    if not t:
        return None
    hit = re.search(r"(PRJ-[A-Z0-9]+)", t)
    if hit:
        return hit.group(1)
    for pid, names in index:
        for n in names:
            core = re.sub(r"[^0-9A-Za-z가-힣]", "", n)
            if core and core[:6] in re.sub(r"[^0-9A-Za-z가-힣]", "", t):
                return pid
    return None


def mk(title, *, pid, state, stream, ref, owner=None, due=None,
       issue=None, when=None, done_at=None) -> dict:
    return {
        "task_id": task_id(pid, title), "title": title,
        "project_id": pid, "project_name": None, "owner": owner, "due": due,
        "state": state, "confidence": "ledger", "issue": issue,
        "source": {"stream": stream, "ref": ref, "when": when,
                   "by": None, "quote": title},
        "opened_at": None, "done_at": done_at,
        "reported_at": None, "archived_at": None,
    }


# --------------------------------------------------------------------- 주간보고

def from_weekly(index) -> list[dict]:
    """가장 최근 주차의 연구기획사업부 보고서만 읽는다.

    과거 주차까지 넣으면 이미 지난 계획이 `planned` 로 되살아난다.
    보드는 **지금**을 보여주는 것이지 이력을 쌓는 곳이 아니다.
    """
    best = _W.latest_report(WEEKLY, org=ORG)
    if not best:
        return []
    for src, wk, real in _W.mismatched(WEEKLY, ORG):
        print(f"  ! 주차 불일치 — 페이지 {wk} 에 {real} 첨부({src}). 실제 보고일로 읽는다")

    head = best.get("header", [])
    def col(pat):
        for h in head:
            if re.search(pat, h):
                return h
        return None
    c_pjt, c_past = col(r"PJT|과제"), col(r"전주|실적")
    c_next, c_iss = col(r"금주|계획"), col(r"이슈")
    ref = best.get("source_file") or f"주간보고 {best.get('week')}"
    # 페이지 주차(week)가 아니라 **첨부가 실제로 담은 보고일**을 쓴다. 이걸 틀리면
    # 지난주 완료가 이번 주 완료로 다시 등재돼 정산·아카이브를 무한히 왕복한다.
    week = best.get("_보고일") or best.get("week")
    year = int((week or "2026")[:4])
    out = []
    for it in best["items"]:
        pid = match_project(it.get(c_pjt, ""), index) if c_pjt else None
        pjt = norm(it.get(c_pjt, "")) if c_pjt else ""
        for c, state in ((c_past, "done"), (c_next, "planned"), (c_iss, "blocked")):
            if not c:
                continue
            for line, day in cell_items(it.get(c), year):
                title = f"{pjt} — {line}" if pjt and pjt not in line else line
                out.append(mk(title, pid=pid, state=state, stream="weekly_report",
                              ref=ref, when=day or week,
                              due=day if state in ("planned", "blocked") else None,
                              done_at=(day or week) if state == "done" else None,
                              issue={"needs": "주간보고", "label": "이슈"}
                              if state == "blocked" else None))
    return out


# ------------------------------------------------------------------- 액션 원장

def from_actions(index) -> list[dict]:
    if not ACTIONS.exists():
        return []
    out = []
    for a in json.loads(ACTIONS.read_text(encoding="utf-8")):
        title = norm(a.get("action"))
        if not title:
            continue
        done = a.get("status") == "done"
        raw_due = norm(a.get("due"))
        # `~08/18`·`미정`·`서버 확보 후` 가 섞여 있다. 날짜로 읽히는 것만 due 로 쓰고
        # 나머지는 **버리지 않고** title 에 남겨 사람이 읽게 둔다.
        due = parse_due(raw_due)
        # 제목 꼬리는 **옛 규칙(M/D 만) 그대로** 붙인다. task_id 가 제목의 해시라서
        # 제목이 바뀌면 같은 일이 원장에 두 줄로 남는다 — 기한을 살리려다 중복을
        # 만들 수는 없다. 제목 정규화는 별건(task_id 재설계)으로 다룬다.
        if raw_due and not _md_due(raw_due):
            title = f"{title} (기한: {raw_due})"
        out.append(mk(title, pid=match_project(title, index),
                      state="done" if done else "planned",
                      stream="action_ledger", ref=a.get("meeting_id"),
                      owner=norm(a.get("owner")) or None, due=due,
                      when=a.get("meeting_id"), done_at=None))
    return out


# ---------------------------------------------------------------------- 캘린더

def from_calendar(index, horizon_days: int = 45) -> list[dict]:
    """앞으로 45일 안의 **마감성** 일정만. 지난 일정은 완료 증거가 아니다 —
    회의가 열렸다는 사실이 그 일이 끝났다는 뜻은 아니기 때문이다."""
    if not CAL.is_dir():
        return []
    today = dt.date.today()
    limit = today + dt.timedelta(days=horizon_days)
    seen, out = set(), []
    for f in CAL.glob("*/*.json"):
        d = json.loads(f.read_text(encoding="utf-8"))
        for ev in d.get("events", []):
            for c in ev.get("eventComponents", []):
                title = norm(c.get("summary"))
                if not title or not CAL_KEYWORDS.search(title):
                    continue
                s = (c.get("start") or {})
                day = (s.get("dateTime") or s.get("date") or "")[:10]
                if not day:
                    continue
                try:
                    dd = dt.date.fromisoformat(day)
                except ValueError:
                    continue
                if not (today <= dd <= limit):
                    continue
                key = (title, day)
                if key in seen:
                    continue
                seen.add(key)
                out.append(mk(title, pid=match_project(title, index), state="planned",
                              stream="calendar", ref=f"{f.parent.name}/{f.stem}",
                              owner=((c.get("organizer") or {}).get("displayName") or None),
                              due=day, when=day))
    return out


def main() -> int:
    index = project_index()
    groups = {"weekly_report": from_weekly(index),
              "action_ledger": from_actions(index),
              "calendar": from_calendar(index)}
    cands = [t for g in groups.values() for t in g]

    out = PROJ / "data" / "tasks" / "candidates.jsonl"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("".join(json.dumps(t, ensure_ascii=False) + "\n" for t in cands),
                   encoding="utf-8")
    print(f"· {out.relative_to(PROJ)} — 후보 {len(cands)}건")
    for k, g in groups.items():
        linked = sum(1 for t in g if t["project_id"])
        print(f"  {k:15} {len(g):3}건 (과제 연결 {linked})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
