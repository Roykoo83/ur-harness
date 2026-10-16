#!/usr/bin/env python3
"""Roy의 답을 지식으로 기록한다 (성장 루틴의 닫는 반쪽).

    python3 scripts/tasks/learn.py list
    python3 scripts/tasks/learn.py answer --q Q-xxxxxxxx --say "답 내용"
        [--alias "PRJ-N021=사례비,PRJ-N016=서안나"]     별칭 사전 추가
        [--allow-messenger "<regex>"]                   메신저 필터 확장
        [--allow-mail "<regex>"]                        메일 필터 확장
        [--deny "<regex>"]                              소음 차단
        [--task]                                        답을 근거로 태스크도 만든다 (apply.py 안내)
    python3 scripts/tasks/learn.py dismiss --q Q-xxxxxxxx [--say "이유"]

답은 rules.md(사람용 원장)와 aliases/patterns.json(기계 적용)에 동시에 남는다.
다음 사이클부터 같은 종류는 질문 없이 처리된다.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import knowledge as K  # noqa: E402


def show(q: dict) -> str:
    return (f"{q['qid']} [{q['type']}] ({q['day']} · {q['source']})\n"
            f"    원문: {q['text'][:120]}\n    왜: {q['why']}\n    질문: {q['ask']}")


def cmd_list() -> int:
    rows = [q for q in K.read_questions() if q["status"] == "open"]
    if not rows:
        print("열린 질문 없음")
        return 0
    for q in rows:
        print(show(q) + "\n")
    print(f"열린 질문 {len(rows)}건")
    return 0


def _update_json(path: Path, mutate) -> None:
    d = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    mutate(d)
    path.write_text(json.dumps(d, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def cmd_answer(a) -> int:
    rows = K.read_questions()
    hit = next((q for q in rows if q["qid"] == a.q), None)
    if not hit:
        print(f"질문 없음: {a.q}")
        return 2
    hit["status"] = "dismissed" if a.mode == "dismiss" else "answered"
    hit["answer"] = a.say or ""
    hit["answered_at"] = dt.date.today().isoformat()
    K.write_questions(rows)

    applied = []
    if a.alias:
        def add_alias(d):
            for pair in a.alias.split(","):
                pid, al = pair.split("=", 1)
                d[al.strip()] = pid.strip()
                applied.append(f"별칭 {al.strip()} → {pid.strip()}")
        _update_json(K.ALIASES, add_alias)
    for key, val in (("messenger_allow", a.allow_messenger),
                     ("mail_allow", a.allow_mail), ("deny", a.deny)):
        if val:
            _update_json(K.PATTERNS, lambda d, k=key, v=val: d.setdefault(k, []).append(v))
            applied.append(f"{key} += {val}")

    with K.RULES.open("a", encoding="utf-8") as f:
        f.write(f"\n### {hit['qid']} ({dt.date.today()}) — "
                f"{'기각' if a.mode == 'dismiss' else '답변'}\n"
                f"- 질문[{hit['type']}]: {hit['text'][:160]}\n"
                f"- Roy: {a.say or '(기록 없음)'}\n")
        for x in applied:
            f.write(f"- 적용: {x}\n")

    print(f"기록 완료 {hit['qid']}" + (f" · 규칙 {len(applied)}건 적용" if applied else ""))
    if a.task:
        print("태스크 등록은: python3 scripts/tasks/apply.py --find \"...\" --state ... 사용")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="mode", required=True)
    sub.add_parser("list")
    for name in ("answer", "dismiss"):
        p = sub.add_parser(name)
        p.add_argument("--q", required=True)
        p.add_argument("--say", default="")
        p.add_argument("--alias")
        p.add_argument("--allow-messenger", dest="allow_messenger")
        p.add_argument("--allow-mail", dest="allow_mail")
        p.add_argument("--deny")
        p.add_argument("--task", action="store_true")
    a = ap.parse_args()
    return cmd_list() if a.mode == "list" else cmd_answer(a)


if __name__ == "__main__":
    raise SystemExit(main())
