#!/usr/bin/env python3
"""주간보고 파싱본 고르기 — 한 곳에서만 정한다.

「가장 최근 주차」를 네 곳(extract·inject·weekly_draft·make_weekly_pptx)이 각자
구현하고 있었다. 판정이 어긋나 2026-09-08 주간회의 화면이 지난주(260901) 표를
「이번 주 최신 제출본」으로 보였고, 이미 정산해 내려간 완료 7건이 같은 날 되살아났다.

**주차의 정본은 페이지가 아니라 첨부 파일명이다.** 컨플루언스 페이지(260908)에
지난주 첨부(260901.pptx)가 걸려 있으면 그 안의 내용은 지난주 것이다. 페이지 주차를
믿으면 지난주 계획이 이번 주 계획으로, 지난주 실적이 이번 주 실적으로 다시 읽힌다.
"""
from __future__ import annotations

import datetime as dt
import json
import re
from pathlib import Path

ORG = "연구기획사업부"

# 파일명 날짜. 8자리(20260901)를 먼저 보고 6자리(260901)를 본다 —
# 6자리부터 보면 「20260901」의 앞 여섯 자를 26년 09월로 잘못 읽는다.
_DATE_PATTERNS = (re.compile(r"(?<!\d)(20\d{2})[-_.]?(\d{2})[-_.]?(\d{2})(?!\d)"),
                  re.compile(r"(?<!\d)(\d{2})(\d{2})(\d{2})(?!\d)"))


def report_date(d: dict) -> str:
    """이 파싱본이 실제로 담고 있는 보고일 (YYYY-MM-DD).

    파일명 날짜가 정본이고, 읽히지 않을 때만 페이지 주차(week)로 물러선다.
    """
    src = d.get("source_file") or ""
    for pat in _DATE_PATTERNS:
        m = pat.search(src)
        if not m:
            continue
        y, mo, day = (int(x) for x in m.groups())
        if y < 100:
            y += 2000
        try:
            return dt.date(y, mo, day).isoformat()
        except ValueError:
            continue
    return d.get("week") or ""


def parsed_reports(root: Path, org: str = ORG) -> list[dict]:
    """해당 부서의 파싱본 전부. 각 dict 에 `_보고일` 을 붙여 돌려준다."""
    out = []
    for f in sorted(root.glob("*.json")):
        try:
            d = json.loads(f.read_text(encoding="utf-8"))
        except (ValueError, OSError):
            continue                       # 색인·목록 형태 파일이 섞여 있다
        if not isinstance(d, dict) or d.get("org") != org or not d.get("items"):
            continue
        d["_보고일"] = report_date(d)
        out.append(d)
    return out


def latest_report(root: Path, before: str = "9999", org: str = ORG) -> dict | None:
    """가장 최근 제출본. `before` 를 주면 그 날짜까지만 본다 (R15 컷오프).

    같은 보고일이 둘이면(같은 첨부가 두 페이지에 걸린 경우) 어느 쪽을 골라도
    내용이 같으므로 먼저 만난 것을 쓴다.
    """
    best = None
    for d in parsed_reports(root, org):
        if d["_보고일"] and d["_보고일"] > before:
            continue
        if best is None or d["_보고일"] > best["_보고일"]:
            best = d
    return best


def mismatched(root: Path, org: str = ORG) -> list[tuple[str, str, str]]:
    """(파일명, 페이지 주차, 실제 보고일) — 둘이 어긋난 파싱본.

    이번 주 보고서가 수집되지 않았는데 지난주 첨부가 이번 주 페이지에 걸린 상황을
    **조용히 넘기지 않는다.** 화면과 로그에 그대로 보인다.
    """
    out = []
    for d in parsed_reports(root, org):
        wk = d.get("week") or ""
        if wk and d["_보고일"] and d["_보고일"] != wk:
            out.append((d.get("source_file") or "", wk, d["_보고일"]))
    return out
