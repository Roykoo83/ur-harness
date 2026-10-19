#!/usr/bin/env python3
"""지식 체계 로더 — 질문·답 루틴으로 성장하는 규칙들 (운영자 지시 2026-08-31).

한 번에 결정하지 않는다. 애매한 맥락은 질문이 되고, 운영자의 답이 여기 규칙으로
쌓여 다음 사이클부터 기계가 적용한다.

    data/knowledge/aliases.json   — 과제 별칭 사전 {별칭(정규화): PRJ-ID}
    data/knowledge/patterns.json  — 필터 확장 {messenger_allow, mail_allow, deny}
    data/knowledge/rules.md       — 사람이 읽는 규칙 원장 (질문·답·날짜 근거 포함)
"""
from __future__ import annotations

import json
import re
import unicodedata
from pathlib import Path

PROJ = Path(__file__).resolve().parents[2]
KDIR = PROJ / "data" / "knowledge"
ALIASES = KDIR / "aliases.json"
PATTERNS = KDIR / "patterns.json"
RULES = KDIR / "rules.md"
QFILE = PROJ / "data" / "tasks" / "questions.jsonl"


def norm(s: str) -> str:
    return re.sub(r"[^0-9a-z가-힣]", "",
                  unicodedata.normalize("NFC", s or "").lower())


def aliases() -> list[tuple[str, str]]:
    """(별칭, PRJ-ID) — 긴 별칭 먼저 (짧은 것에 잡아먹히지 않게)."""
    if not ALIASES.exists():
        return []
    d = json.loads(ALIASES.read_text(encoding="utf-8"))
    out = [(norm(a), pid) for a, pid in d.items() if not a.startswith("_")]
    return sorted(out, key=lambda x: -len(x[0]))


def patterns() -> dict[str, list[re.Pattern]]:
    keys = ("messenger_allow", "mail_allow", "deny")
    if not PATTERNS.exists():
        return {k: [] for k in keys}
    d = json.loads(PATTERNS.read_text(encoding="utf-8"))
    return {k: [re.compile(p) for p in d.get(k, []) if not p.startswith("_")]
            for k in keys}


def read_questions() -> list[dict]:
    if not QFILE.exists():
        return []
    return [json.loads(x) for x in QFILE.read_text(encoding="utf-8").splitlines()
            if x.strip()]


def write_questions(rows: list[dict]) -> None:
    QFILE.parent.mkdir(parents=True, exist_ok=True)
    QFILE.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows),
                     encoding="utf-8")
