#!/usr/bin/env python3
"""사람이 알려준 상태를 원장에 반영한다.

    python3 scripts/tasks/apply.py --find "제3자 테스트"
    python3 scripts/tasks/apply.py --id T-1234abcd --state done --note "집행 완료" --by Roy

왜 별도 도구인가 — 원장을 손으로 고치면 다음 수집이 덮어쓴다. 정정은 **가장 높은 신뢰도의
증거**로 들어가야 하고, 그래야 추출기가 만든 값보다 우선한다.

`--state` 없이 `--note` 만 주면 상태는 그대로 두고 메모만 붙는다.
`--clear-check` 는 「확인 필요」 표시만 지운다 — 사람이 확인해 줬다는 뜻이다.

봇 한 줄 정정(`@Urban AX Bot T-xxxx 완료`)도 결국 이 함수를 부르게 된다.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import re
from pathlib import Path

PROJ = Path(__file__).resolve().parents[2]
D = PROJ / "data" / "tasks"
LEDGER, EVENTS = D / "ledger.jsonl", D / "events.jsonl"
STATES = ("planned", "doing", "blocked", "done")


def read(p: Path) -> list[dict]:
    if not p.exists():
        return []
    return [json.loads(x) for x in p.read_text(encoding="utf-8").splitlines() if x.strip()]


def write(p: Path, rows) -> None:
    p.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows),
                 encoding="utf-8")


def norm(s: str) -> str:
    return re.sub(r"[^0-9A-Za-z가-힣]", "", s or "").lower()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--find")
    ap.add_argument("--id")
    ap.add_argument("--state", choices=STATES)
    ap.add_argument("--note")
    ap.add_argument("--by", default="Roy")
    ap.add_argument("--clear-check", action="store_true")
    ap.add_argument("--owner")
    ap.add_argument("--due")
    ap.add_argument("--title", help="제목 자체가 낡았을 때 — 사람이 준 문장으로 교체")
    ap.add_argument("--new", help="새 태스크 제목 — 사람이 알려준 사실을 원장에 직접 넣는다")
    ap.add_argument("--project", help="--new 와 함께: PRJ-ID")
    ap.add_argument("--issue-label", help="--new 와 함께: 대기·관계자간·부서간 등 (blocked 일 때)")
    a = ap.parse_args()

    rows = read(LEDGER)

    if a.new:
        import hashlib
        today = dt.date.today().isoformat()
        key = f"{a.project or '-'}|{re.sub(r'[^0-9A-Za-z가-힣]', '', a.new)}"
        tid = "T-" + hashlib.sha1(key.encode()).hexdigest()[:10]
        if any(t["task_id"] == tid for t in rows):
            print(f"이미 있음: {tid}")
            return 1
        state = a.state or "planned"
        t = {"task_id": tid, "title": a.new, "state": state,
             "project_id": a.project, "owner": a.owner, "due": a.due,
             "done_at": today if state == "done" else None,
             "opened_at": today, "reported_at": today,
             "needs_check": None, "confidence": "ledger",
             "issue": ({"label": a.issue_label or "대기", "needs": None}
                       if state == "blocked" else None),
             "source": {"stream": "correction", "ref": a.by, "when": today},
             "evidence": [{"stream": "correction", "ref": a.by, "when": today,
                           "quote": a.note or ""}],
             "notes": ([{"at": today, "by": a.by, "text": a.note}] if a.note else [])}
        rows.append(t)
        write(LEDGER, rows)
        ev = read(EVENTS)
        ev.append({"at": today, "task_id": tid, "what": "신규(정정)", "state": state,
                   "title": a.new[:80], "by": a.by})
        write(EVENTS, ev)
        print(f"· 신규 {tid} [{state}] {a.new[:60]}")
        return 0
    if a.find:
        q = norm(a.find)
        for t in rows:
            if q in norm(t["title"]):
                flag = "⚠" if t.get("needs_check") else " "
                print(f'{flag} {t["task_id"]} [{t["state"]:8}] '
                      f'{(t.get("project_id") or "-"):10} {t["title"][:78]}')
        return 0

    if not a.id:
        ap.error("--id 또는 --find 가 필요하다")
    hit = [t for t in rows if t["task_id"] == a.id]
    if not hit:
        print(f"없는 태스크: {a.id}")
        return 1
    t = hit[0]

    today = dt.date.today().isoformat()
    changed = []
    if a.state and a.state != t["state"]:
        changed.append(f'{t["state"]}→{a.state}')
        t["state"] = a.state
        t["done_at"] = today if a.state == "done" else None
        if a.state != "blocked":
            t["issue"] = None            # 풀렸으면 이슈도 내린다
    if a.owner:
        t["owner"] = a.owner
        changed.append(f"owner={a.owner}")
    if a.due:
        t["due"] = a.due
        changed.append(f"due={a.due}")
    if a.title:
        changed.append(f'제목: {t["title"][:24]}…→{a.title[:24]}')
        t["title"] = a.title
    if a.project:
        t["project_id"] = a.project
        changed.append(f"과제={a.project}")
    if a.clear_check or a.state:
        if t.pop("needs_check", None):
            changed.append("확인표시 해제")
    if a.note:
        t.setdefault("notes", []).append({"at": today, "by": a.by, "text": a.note})
        changed.append("메모")

    # 사람이 말한 것이 가장 세다. 다음 수집이 덮어쓰지 못하도록 신뢰도를 올린다.
    t["confidence"] = "ledger"
    t.setdefault("evidence", []).append(
        {"stream": "correction", "ref": a.by, "when": today, "quote": a.note or ""})

    write(LEDGER, rows)
    ev = read(EVENTS)
    ev.append({"at": today, "task_id": t["task_id"], "what": " · ".join(changed) or "정정",
               "state": t["state"], "title": t["title"][:80], "by": a.by})
    write(EVENTS, ev)
    print(f'· {t["task_id"]} {" · ".join(changed) or "정정"} — {t["title"][:60]}')
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
