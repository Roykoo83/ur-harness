#!/usr/bin/env python3
"""애매한 맥락 → Roy 질문 (성장 루틴, Roy 지시 2026-08-31).

    python3 scripts/tasks/questions.py

기계가 확신 못 하는 것을 버리지 않고 질문으로 만든다. 세 갈래:
    1. 메신저 경계 — 상태 키워드는 없지만 과제·돈·기한 신호가 있는 발화
    2. 메일 경계   — 제목 필터에 걸리지 않았지만 사내 발신이거나 과제·돈·기한 신호
    3. 과제 미연결 — 후보로는 뽑혔는데 어느 과제인지 모르는 항목

멱등: 질문 ID는 내용 해시. 이미 답한(또는 기각한) 질문은 다시 만들지 않는다.
답은 learn.py 로 기록하면 규칙(aliases/patterns)이 되어 다음 사이클부터 적용된다.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import knowledge as K  # noqa: E402
import extract_observed as EO  # noqa: E402

MONEY_RE = re.compile(r"\d[\d,.]*\s*(?:원|만원|천원|백만|억)")
DEADLINE_RE = re.compile(r"마감|까지|~\s*\d{1,2}시|\d{1,2}/\d{1,2}\s*까지")
NOISE_SENDER = re.compile(r"no-?reply|newsletter|billing|support|notify|info@|@email\.|@team\.", re.I)
MAX_OPEN = 30          # 파일에 쌓이는 열린 질문 상한 — 넘치면 오래된 것부터 자동 기각


def qid(kind: str, text: str) -> str:
    return "Q-" + hashlib.sha1(f"{kind}|{K.norm(text)[:80]}".encode()).hexdigest()[:8]


def alias_hit(text: str) -> str | None:
    t = K.norm(text)
    for al, pid in K.aliases():
        if al in t:
            return pid
    return None


def mk(kind, text, *, day, src, why, ask) -> dict:
    return {"qid": qid(kind, text), "type": kind, "asked_at": dt.date.today().isoformat(),
            "day": day, "source": src, "text": text[:300], "why": why, "ask": ask,
            "status": "open"}


def from_messenger() -> list[dict]:
    if not EO.MSG.is_dir():
        return []
    since = (dt.date.today() - dt.timedelta(days=EO.WINDOW_DAYS)).isoformat()
    pat = K.patterns()
    out = []
    for f in sorted(EO.MSG.glob("*/*.jsonl")):
        for line in f.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            m = json.loads(line)
            day = (m.get("kst") or "")[:10]
            text = re.sub(r"\s+", " ", (m.get("text") or "")).strip()
            if day < since or len(text) < EO.MIN_LEN or text.startswith("🔔"):
                continue
            if EO.DONE_RE.search(text) or EO.ASK_RE.search(text) \
                    or EO.BLOCK_RE.search(text):
                continue                       # 이미 후보가 된다
            if any(p.search(text) for p in pat["messenger_allow"]) \
                    or any(p.search(text) for p in pat["deny"]):
                continue                       # 이미 규칙이 있다
            pid = alias_hit(text)
            signal = []
            if pid:
                signal.append(f"과제 신호 {pid}")
            if MONEY_RE.search(text):
                signal.append("금액")
            if DEADLINE_RE.search(text):
                signal.append("기한")
            if not signal:
                continue                       # 신호 없는 잡담은 질문도 아니다
            out.append(mk("메신저 경계", text, day=day,
                          src=f"부서방 · {m.get('sender', '?')}",
                          why=f"{' · '.join(signal)} 신호가 있는데 완료·요청·막힘 표현이 없어 상태를 확신 못 함",
                          ask="보드에 올릴까요? 올린다면 진행·이슈 중 무엇이고 어느 과제입니까?"))
    return out


def from_mail() -> list[dict]:
    if not EO.MAIL.is_dir():
        return []
    since = (dt.date.today() - dt.timedelta(days=EO.WINDOW_DAYS)).isoformat()
    pat = K.patterns()
    out, seen = [], set()
    for f in EO.MAIL.glob("*/*/[0-9]*.json"):
        try:
            d = json.loads(f.read_text())
        except (ValueError, OSError):
            continue
        mo = d.get("mail") or d
        day = (mo.get("receivedTime") or "")[:10]
        if day < since:
            continue
        subj = re.sub(r"\s+", " ", (mo.get("subject") or "")).strip()
        subj = re.sub(r"^(?:(?:re|fw|fwd|답장|전달)\s*[:：]\s*)+", "", subj, flags=re.I)
        sender = (mo.get("from") or {}).get("email", "")
        if len(subj) < EO.MIN_LEN or subj in seen:
            continue
        seen.add(subj)
        if EO.ASK_RE.search(subj) or "요청" in subj or "마감" in subj or "제출" in subj:
            continue                           # 이미 후보가 된다
        if any(p.search(subj) for p in pat["mail_allow"]) \
                or any(p.search(subj) for p in pat["deny"]):
            continue
        if NOISE_SENDER.search(sender) or "(광고)" in subj:
            continue
        pid = alias_hit(subj)
        internal = sender.endswith("{{ORG_DOMAIN}}")
        if not (pid or internal or MONEY_RE.search(subj) or DEADLINE_RE.search(subj)):
            continue
        why = " · ".join(x for x in (
            f"과제 신호 {pid}" if pid else "", "사내 발신" if internal else "",
            "금액" if MONEY_RE.search(subj) else "",
            "기한" if DEADLINE_RE.search(subj) else "") if x)
        out.append(mk("메일 경계", subj, day=day, src=f"메일 · {sender}",
                      why=f"{why} 신호가 있는데 요청·마감·제출류 제목이 아니라 필터를 통과 못 함",
                      ask="이 종류의 메일을 보드에 올릴까요? 어느 과제·영역입니까?"))
    return out


def from_unmatched() -> list[dict]:
    cand = EO.PROJ / "data" / "tasks" / "candidates_observed.jsonl"
    if not cand.exists():
        return []
    out = []
    for line in cand.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        t = json.loads(line)
        if t.get("project_id"):
            continue
        src = t.get("source") or {}
        out.append(mk("과제 미연결", t["title"],
                      day=str(src.get("when") or "")[:10],
                      src=f"{src.get('stream', '?')} · {t.get('owner') or ''}",
                      why="후보로는 뽑혔지만 어느 과제인지 연결하지 못해 §01 카드에 못 실림",
                      ask="어느 과제입니까? (별칭을 알려주시면 사전에 등록합니다)"))
    return out


def main() -> int:
    old = {q["qid"]: q for q in K.read_questions()}
    fresh = from_messenger() + from_mail() + from_unmatched()
    added = 0
    for q in fresh:
        if q["qid"] not in old:
            old[q["qid"]] = q
            added += 1
    rows = sorted(old.values(), key=lambda q: (q["asked_at"], q["qid"]))
    # 열린 질문이 상한을 넘으면 오래된 것부터 만료 — 질문이 밀리면 성장이 아니라 소음이다
    open_qs = [q for q in rows if q["status"] == "open"]
    for q in open_qs[:-MAX_OPEN] if len(open_qs) > MAX_OPEN else []:
        q["status"] = "expired"
    K.write_questions(rows)
    n_open = sum(1 for q in rows if q["status"] == "open")
    print(f"· 성장 질문 — 신규 {added} · 열림 {n_open} · 누적 {len(rows)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
