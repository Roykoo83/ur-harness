#!/usr/bin/env python3
"""관측 계열 스트림 → 태스크 후보 (P2).

    python3 scripts/tasks/extract_observed.py

P1 은 사람이 문서에 써 둔 것만 읽었다. 그런데 **일은 문서에 쓰이기 전에 일어난다.**
최근 사흘 SSOT 에만 147개 파일이 바뀌었는데 보드에는 한 줄도 없었다.

여기서 읽는 세 가지
    1. SSOT 산출물   — 드라이브에 파일이 놓였다 = 그 일이 진행됐다
    2. 부서방 발화   — 완료·요청·막힘을 사람이 말한 것
    3. 메일          — 마감·요청

**전부 `observed` 다.** 사람이 "이건 태스크다"라고 선언한 적이 없으므로 원장 계열보다
한 단계 낮게 다루고, 애매하면 태스크로 만들지 않는다.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import re
import unicodedata
from pathlib import Path

PROJ = Path(__file__).resolve().parents[2]
REPO = PROJ.parents[1]
SSOT = (Path.home() / "{{MOUNT_REL}}"
        / "공유 드라이브" / "{{SSOT_DRIVE}}")
# 아카이브되는 모든 메신저 방(dept·과제예산 등)을 읽는다. 과제예산방이
# 아카이브만 되고 추출에서 빠져 있었다 (2026-09-04 Roy 확인 질문으로 발견).
MSG = REPO / "projects/nworks-agent/output/raw_messenger"
MAIL = REPO / "projects/nworks-agent/output/raw_mail"

WINDOW_DAYS = 7

import sys as _sys
_sys.path.insert(0, str(Path(__file__).resolve().parent))
import knowledge as _K  # noqa: E402  — 별칭·필터 규칙 (질문·답 루틴으로 성장)

# 기계가 만든 파일은 일의 증거가 아니다. `_INDEX.md` 는 색인기가, `files.tsv` 는
# 스캐너가 쓴다. 이걸 태스크로 세면 **우리 도구가 일한 것을 사람이 일한 것으로 센다.**
SKIP_NAMES = {"_INDEX.md", "files.tsv", "manifest.json", "보안대장.tsv", "graph.json",
              "_README.md"}   # 폴더 안내문은 비계(飛階)다 — 일의 증거가 아니다 (R10)
SKIP_DIRS = {"_시스템", "50_대시보드", "90_보관", ".shortcut-targets-by-id"}

# 부서방 발화 판정. 짧은 맞장구("네", "확인했습니다")는 태스크가 아니다.
DONE_RE = re.compile(r"(완료|끝났|끝냈|제출했|발송했|전달했|반영했|올렸|처리했|집행했|납부했)")
# 해소·결과 통보 — "가능하다고 합니다"류. 계획·문의가 있었다면 이것이 그 결과다.
# 과제 신호가 함께 있을 때만 결과로 등재한다 (R8, Roy 답 2026-08-31 Q2·Q3).
RESOLVE_RE = re.compile(r"(가능하다고|가능하여|된다고 합니다|되었다고|확인되었|승인되|해결되|정리되)")
ASK_RE = re.compile(r"(요청드립니다|부탁드립니다|필요합니다|해주세요|확인해\s*주|검토\s*부탁)")
# 운영 OS 봇이 보낸 알림을 사람이 부서방에 옮겨 붙인 것. 그 안의 문장은 **이미 원장에
# 있는 태스크를 되읽은 것**이라 새 일이 아니다. 그대로 두면 봇 문구가 제목이 되어
# 「🔔 운영 OS — 판단이 필요한 건이 있습니다」가 태스크로 원장에 앉는다.
BOT_RELAY = re.compile(r"🔔\s*운영\s*OS")
# 구글이 자동으로 보내는 공유·댓글·일정 알림. 기관 공고 메일에도 noreply 주소가 흔하므로
# **주소 전체를 막지 않고** 이 발신자들만 집는다.
MACHINE_SENDER = re.compile(
    r"(drive-shares[^@]*@google\.com|calendar-notification@google\.com|"
    r"comments-noreply@docs\.google\.com|no-?reply@(?:google|docs\.google)\.com)", re.I)
# 「(~9/9(수))」 처럼 제목에 박힌 기한. 버리면 내일 마감인 건이 기한 목록에 뜨지 않는다.
SUBJ_DUE = re.compile(r"~\s*(\d{1,2})\s*/\s*(\d{1,2})")
BLOCK_RE = re.compile(r"(대기\s*중|지연|보류|막혔|안\s*됩니다|불가|미정입니다|답이\s*없)")
MIN_LEN = 18


def subj_due(subj: str) -> str | None:
    """제목 꼬리의 기한 → 날짜. 범위(`8/1~9/9`)면 끝날짜가 기한이다."""
    m = SUBJ_DUE.search(subj or "")
    if not m:
        return None
    try:
        return dt.date(dt.date.today().year, int(m.group(1)), int(m.group(2))).isoformat()
    except ValueError:
        return None


def nfc(s: str) -> str:
    """맥 드라이브의 한글 파일명은 NFD 다. 비교하려면 NFC 로 맞춰야 한다."""
    return unicodedata.normalize("NFC", s)


def task_id(pid, title) -> str:
    key = f"{pid or '-'}|{re.sub(r'[^0-9A-Za-z가-힣]', '', title)}"
    return "T-" + hashlib.sha1(key.encode()).hexdigest()[:10]


def mk(title, *, pid, state, stream, ref, owner=None, due=None,
       when=None, done_at=None, quote=None) -> dict:
    return {
        "task_id": task_id(pid, title), "title": title,
        "project_id": pid, "project_name": None, "owner": owner, "due": due,
        "state": state, "confidence": "observed", "issue": None,
        "source": {"stream": stream, "ref": ref, "when": when,
                   "by": owner, "quote": quote or title},
        "opened_at": None, "done_at": done_at,
        "reported_at": None, "archived_at": None,
    }


# ------------------------------------------------------------------ SSOT 산출물

# 분류용 폴더는 일의 단위가 아니다. 「1_수행중」이 바뀌었다는 말은 아무 뜻이 없다.
BUCKETS = {"1_수행중", "2_지원중", "3_종료", "3_종료·정산", "2_실주", "10_과제",
           "00_기준문서", "원본"}
# 한 번에 수백 개가 바뀌는 곳 — 온톨로지 생성기(354건)·통신 아카이브(482건)가 쓴다.
# 사람이 하루에 60~70개를 만지는 일은 실제로 있으므로(IRB 제출일 66건) 문턱은 그 위에 둔다.
BULK = 150


def scope_of(parts: list[str]) -> str | None:
    """경로에서 **일의 단위**를 집는다. 못 집으면 None — 억지로 만들지 않는다.

    받아들이는 것은 둘뿐이다.
      · 과제 폴더 (`…/10_과제/1_수행중/<과제>/…`)
      · 최상위 Cowork 프로젝트 폴더 (`Medicus_…`, `Pathology_AI` — 숫자·밑줄로 시작하지 않는다)
    나머지(공통 문서·온톨로지·통신 아카이브)는 산출물이 아니라 기록이다.
    """
    if len(parts) < 2:
        return None                                   # 최상위 낱파일
    if "10_과제" in parts:
        i = parts.index("10_과제")
        for p in parts[i + 1:-1]:
            if p not in BUCKETS:
                return p
        return None
    top = parts[0]
    if top[0].isdigit() or top.startswith("_"):
        return None                                   # 00_공통·10_부서 등 관리 구획
    return top                                        # Cowork 프로젝트 폴더


def from_drive(index, days: int = WINDOW_DAYS) -> list[dict]:
    """드라이브 변경을 **(과제 폴더 + 날짜)** 한 건으로 묶는다.

    파일 하나를 태스크 하나로 세면 안 된다. 한 번 작업하면 파일이 열 개씩 나온다 —
    실제로 사흘에 147개가 바뀌었고, 그대로 세면 보드가 파일 목록이 된다.
    """
    if not SSOT.is_dir():
        return []
    since = dt.datetime.now() - dt.timedelta(days=days)
    groups: dict[tuple, dict] = {}

    for f in SSOT.rglob("*"):
        if not f.is_file() or f.name.startswith("."):
            continue
        rel = nfc(str(f.relative_to(SSOT)))
        parts = rel.split("/")
        if nfc(f.name) in SKIP_NAMES or any(p in SKIP_DIRS for p in parts):
            continue
        try:
            m = dt.datetime.fromtimestamp(f.stat().st_mtime)
        except OSError:
            continue
        if m < since:
            continue

        scope = scope_of(parts)
        if scope is None:
            continue

        key = (scope, m.date().isoformat())
        g = groups.setdefault(key, {"files": [], "latest": m})
        g["files"].append(parts[-1])
        g["latest"] = max(g["latest"], m)

    out = []
    for (scope, day), g in groups.items():
        n = len(g["files"])
        if n > BULK:
            # 대량 이관·재생성이지 그날의 작업이 아니다. 세면 보드가 거짓말을 한다.
            print(f"  · {scope} {day} {n}건 — 대량 변경으로 보고 제외")
            continue
        sample = ", ".join(sorted(g["files"])[:3])
        more = f" 외 {n - 3}건" if n > 3 else ""
        title = f"{scope} — 산출물 {n}건 정리 ({sample}{more})"
        out.append(mk(title, pid=match(scope, index), state="done", stream="drive",
                      ref=f"SSOT/{scope}", when=day, done_at=day,
                      quote=f"{day} 드라이브 변경 {n}건"))
    return out


# -------------------------------------------------------------------- 부서방

def from_messenger(index, days: int = WINDOW_DAYS) -> list[dict]:
    if not MSG.is_dir():
        return []
    since = (dt.date.today() - dt.timedelta(days=days)).isoformat()
    out, seen = [], set()
    for f in sorted(MSG.glob("*/*.jsonl")):
        for line in f.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            m = json.loads(line)
            day = (m.get("kst") or "")[:10]
            if day < since:
                continue
            text = re.sub(r"\s+", " ", (m.get("text") or "")).strip()
            if len(text) < MIN_LEN:
                continue
            pat = _K.patterns()
            if BOT_RELAY.search(text) or any(p.search(text) for p in pat["deny"]):
                continue
            if DONE_RE.search(text):
                state, done = "done", day
            elif BLOCK_RE.search(text):
                state, done = "blocked", None
            elif ASK_RE.search(text):
                state, done = "planned", None
            elif RESOLVE_RE.search(text) and match(text, index):
                state, done = "done", day       # 해소·결과 통보 = 결과로 보유 (R8)
            elif any(p.search(text) for p in pat["messenger_allow"]):
                state, done = "planned", None   # Roy가 답으로 허용한 패턴
            else:
                continue
            title = text[:140]
            if title in seen:
                continue
            seen.add(title)
            out.append(mk(title, pid=match(text, index), state=state,
                          stream="messenger", ref=f"{f.parent.name}/{f.stem}", when=day,
                          owner=m.get("sender"), done_at=done, quote=text[:300]))
    return out


# ---------------------------------------------------------------------- 메일

def from_mail(index, days: int = WINDOW_DAYS) -> list[dict]:
    if not MAIL.is_dir():
        return []
    since = (dt.date.today() - dt.timedelta(days=days)).isoformat()
    out, seen = [], set()
    for f in MAIL.glob("*/*/[0-9]*.json"):
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
        pat = _K.patterns()
        if any(p.search(subj) for p in pat["deny"]):
            continue
        sender = (mo.get("from") or {}).get("email", "")
        if MACHINE_SENDER.search(sender):
            continue                       # 「'…회의록.md' 문서가 공유됨」은 일이 아니다
        allow = any(p.search(subj) for p in pat["mail_allow"])   # Roy 허용 패턴
        # R7 (Roy 답 2026-08-31 Q1): 과제명이 식별되는 공유·안내 메일은 자동 등재.
        # "중요한 사안일 수 있습니다" — 과제 신호 + (공유·안내류 제목 or 사내 발신).
        if not allow and match(subj, index) and (
                re.search(r"공유|안내|공지|전달", subj)
                or sender.endswith("{{ORG_DOMAIN}}")):
            allow = True
        if len(subj) < MIN_LEN or (not allow and not ASK_RE.search(subj)
                                   and "요청" not in subj
                                   and "마감" not in subj and "제출" not in subj):
            continue
        if subj in seen:
            continue
        seen.add(subj)
        out.append(mk(subj, pid=match(subj, index), state="planned", stream="mail",
                      ref=sender, when=day, due=subj_due(subj), quote=subj))
    return out


def match(text, index):
    """과제명 **토큰이 전부** 들어 있어야 그 과제로 본다.

    앞 6글자 비교로는 「팁스 R&D 글로벌트랙 (…)」이 「팁스 글로벌트랙」에 안 걸린다 —
    가운데 다른 말이 끼면 연속 문자열이 깨지기 때문이다. 반대로 너무 느슨하면
    「NIPA 디지털전환」이 「NIPA AI바우처」로 잡힌다. 토큰 전부 일치가 그 사이다.

    별칭 사전이 먼저다 — 부서방은 「ax sprint」「칠곡」처럼 부르지 과제 정식명으로
    부르지 않는다. 사전은 질문·답 루틴(learn.py)으로 성장한다.
    """
    t = re.sub(r"[^0-9A-Za-z가-힣]", "", nfc(text or "")).lower()
    if not t:
        return None
    for al, pid in _K.aliases():
        if al in t:
            return pid
    for pid, names in index:
        for n in names:
            toks = [re.sub(r"[^0-9A-Za-z가-힣]", "", x).lower()
                    for x in re.split(r"[\s·,()]+", n) if len(x.strip()) >= 2]
            if toks and all(tok in t for tok in toks):
                return pid
    return None


def main() -> int:
    import extract as E                                   # 과제 색인을 함께 쓴다
    index = E.project_index()
    groups = {"drive": from_drive(index),
              "messenger": from_messenger(index),
              "mail": from_mail(index)}
    rows = [t for g in groups.values() for t in g]

    out = PROJ / "data" / "tasks" / "candidates_observed.jsonl"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("".join(json.dumps(t, ensure_ascii=False) + "\n" for t in rows),
                   encoding="utf-8")
    print(f"· {out.relative_to(PROJ)} — 관측 후보 {len(rows)}건 (최근 {WINDOW_DAYS}일)")
    for k, g in groups.items():
        print(f"  {k:10} {len(g):3}건 (과제 연결 {sum(1 for t in g if t['project_id'])})")
    return 0


if __name__ == "__main__":
    import sys
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    raise SystemExit(main())
