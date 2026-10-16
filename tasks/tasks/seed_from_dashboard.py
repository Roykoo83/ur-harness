#!/usr/bin/env python3
"""손으로 쓴 운영 OS 대시보드에서 태스크를 뽑아 시드 원장을 만든다 (P0, 1회성).

    python3 scripts/tasks/seed_from_dashboard.py

왜 필요한가 — 지금 대시보드의 진행사항·이슈사항은 **사람이 쓴 문장**이고, 생성문보다
낫다. 그래서 P1 생성기로 넘어가기 전에 그 내용을 손실 없이 태스크 레코드로 옮긴다.

**문장을 새로 쓰지 않는다.** 원문을 그대로 `title` 에 넣고, 원문 HTML 도 `source.quote`
에 남긴다. 다시 만들어 낼 때 사람이 쓴 표현이 살아 있어야 한다.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import re
from pathlib import Path

PROJ = Path(__file__).resolve().parents[2]
SRC = PROJ / "output" / "rpbd_운영OS_대시보드.html"
OUT = PROJ / "data" / "tasks"

# 대시보드 태그의 뜻. `c`·`x` 는 라벨 문구로 다시 갈린다.
ISSUE_NEEDS = {"대표님": "대표님컨펌", "부서간": "부서간컨펌", "관계자간": "관계자간컨펌"}
# `d` 는 이슈가 아니라 상태 표시다 — 대기·회신대기 계열만 막힘으로 본다.
BLOCKED_LABELS = {"대기", "회신대기", "확인"}


def strip(html: str) -> str:
    """태그를 걷어낸 순수 문장. 줄바꿈·연속 공백은 한 칸으로 접는다."""
    t = re.sub(r"<[^>]+>", "", html)
    return re.sub(r"\s+", " ", t).replace("&amp;", "&").strip()


def to_date(when: str, base_year: int) -> str | None:
    """`08/20` → `2026-08-20`. `09/02 또는 09/09` 처럼 확정되지 않은 것은 None.

    규칙 0 — 모르는 날짜는 **추측해서 채우지 않는다.** 원문은 title 에 남아 있으므로
    사람이 읽으면 알 수 있고, 시스템은 '기한 미정'으로 다룬다.
    """
    hits = re.findall(r"\b(\d{1,2})/(\d{1,2})\b", when or "")
    if len(hits) != 1:
        return None
    mm, dd = (int(x) for x in hits[0])
    try:
        return dt.date(base_year, mm, dd).isoformat()
    except ValueError:
        return None


def task_id(project_id: str | None, title: str) -> str:
    """(과제 + 정규화 제목) 해시. 같은 내용을 두 번 넣어도 태스크가 늘지 않는다."""
    key = f"{project_id or '-'}|{re.sub(r'[^0-9A-Za-z가-힣]', '', title)}"
    return "T-" + hashlib.sha1(key.encode()).hexdigest()[:10]


# 문맥 표지와 항목을 **등장 순서대로** 훑는다. 카드 블록을 통째로 잘라내는 방식은
# `.card` 밖에 있는 항목(기타 프로젝트 이슈·부서 공통 알림 등 16건)을 놓쳤다.
# 선형 스캔은 무엇이 어디에 있든 태그 달린 `<li>` 를 하나도 빠뜨리지 않는다.
MARK_RE = re.compile(
    r'<div class="id">(?P<pid>.*?)</div>'
    r'|<h4>(?P<h4>.*?)</h4>'
    r'|<div class="sec-head".*?>(?P<sec>.*?)</div>'
    r'|<div class="block-t(?P<btype>[^"]*)">(?P<btitle>.*?)</div>'
    r'|<li>(?P<li>.*?)</li>', re.S)


def parse_cards(html: str, today: dt.date) -> list[dict]:
    tasks: list[dict] = []
    pid = project = None
    is_issue = False

    for m in MARK_RE.finditer(html):
        if m.group("pid") is not None:
            raw = strip(m.group("pid"))
            hit = re.search(r"(PRJ-[A-Z0-9]+)", raw)
            pid = hit.group(1) if hit else None
            continue
        if m.group("h4") is not None:
            project = strip(m.group("h4"))
            continue
        if m.group("sec") is not None:
            # 섹션이 바뀌면 과제 문맥을 버린다 — 앞 카드의 과제가 흘러들면 안 된다
            pid = project = None
            continue
        if m.group("btype") is not None:
            is_issue = "issue" in m.group("btype")
            continue

        li = m.group("li")
        tag = re.search(r'<span class="tag ([a-z])">([^<]*)</span>', li)
        if not tag:
            continue
        kind, label = tag.group(1), tag.group(2).strip()
        wm = re.search(r'<span class="when">(.*?)</span>', li, re.S)
        when = strip(wm.group(1)) if wm else ""
        src = re.search(r'<span class="src">(.*?)</span>', li, re.S)
        body = re.sub(r'<span class="tag [a-z]">[^<]*</span>', "", li)
        body = re.sub(r'<span class="src">.*?</span>', "", body, flags=re.S)
        title = strip(body)
        if not title:
            continue

        date = to_date(when, today.year)
        if is_issue:
            # 이슈 블록 안에도 `F`(앞으로 할 일)가 섞여 있다 — 그건 막힌 게 아니라 계획이다
            state = {"p": "done", "f": "planned"}.get(kind, "blocked")
            # 매핑에 없으면 **사람이 쓴 라벨을 그대로 쓴다.** '미분류'로 뭉개면
            # 「회신대기」와 「검토」가 같은 칸에 들어가 구분이 사라진다.
            issue = {"needs": ISSUE_NEEDS.get(label) or label, "label": label} \
                if state == "blocked" else None
        else:
            state = "done" if kind == "p" else "planned"
            issue = None
            if kind == "d" and label in BLOCKED_LABELS:
                state, issue = "blocked", {"needs": label, "label": label}

        tasks.append({
            "task_id": task_id(pid, title),
            "title": title,
            "project_id": pid,
            "project_name": project,
            "owner": None,            # 대시보드에는 담당자 칸이 없다 — 모르면 모른다
            "due": date if state in ("planned", "blocked") else None,
            "state": state,
            "confidence": "ledger",   # 사람이 직접 쓴 문장이라 최고 신뢰도다
            "issue": issue,
            "source": {
                "stream": "dashboard_seed",
                "ref": SRC.name,
                "when": when or None,
                "by": strip(src.group(1)) if src else None,
                "quote": title,
            },
            "opened_at": None,
            "done_at": date if state == "done" else None,
            "reported_at": None,
            "archived_at": None,
        })
    return tasks


def main() -> int:
    if not SRC.exists():
        print(f"원본 없음: {SRC}")
        return 2
    today = dt.date.today()
    tasks = parse_cards(SRC.read_text(encoding="utf-8"), today)

    seen, uniq = set(), []
    for t in tasks:
        if t["task_id"] in seen:
            continue
        seen.add(t["task_id"])
        uniq.append(t)

    OUT.mkdir(parents=True, exist_ok=True)
    f = OUT / "seed.jsonl"
    f.write_text("".join(json.dumps(t, ensure_ascii=False) + "\n" for t in uniq),
                 encoding="utf-8")

    by = {}
    for t in uniq:
        by[t["state"]] = by.get(t["state"], 0) + 1
    print(f"· {f.relative_to(PROJ)} — 태스크 {len(uniq)}건 (중복 {len(tasks) - len(uniq)}건 접음)")
    print("  상태: " + " · ".join(f"{k} {v}" for k, v in sorted(by.items())))
    print(f"  과제 연결 {sum(1 for t in uniq if t['project_id'])}건 / "
          f"기한 있음 {sum(1 for t in uniq if t['due'])}건 / "
          f"이슈 {sum(1 for t in uniq if t['issue'])}건")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
