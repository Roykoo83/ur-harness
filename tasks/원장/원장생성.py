#!/usr/bin/env python3
"""운영 OS 데이터 원장 — 회차 생성 (아침 08 · 점심 13 · 저녁 17)

    python3 scripts/원장/원장생성.py [--회차 0800|1300|1700] [--dry]

산출물
    output/원장/이력/YYYY-MM/YYYY-MM-DD_HHMM.md   회차 스냅샷 (이력, 지우지 않는다)
    output/원장/최신.md                            고정 이름. 사람과 AI가 읽는 정본
    output/원장/최신.js                            같은 내용. 대시보드가 script src 로 읽는다

왜 이 모양인가
    · **회차마다 「직전 대비 변경」을 적는다.** 스냅샷만 쌓으면 다섯 시간 사이에 일어난 일을
      알려면 두 파일을 비교해야 하고, 주간보고를 만들 때 그 계산을 다시 해야 한다.
      변경분을 회차에 적어 두면 주간보고는 그 주의 변경분을 이어 붙이기만 하면 된다.
    · **결손을 감추지 않는다.** 맥이 꺼져 회차를 놓치면 파일이 없고, 다음 회차 머리에
      결손으로 적힌다. 화면이 「모르는 것을 모른다」고 말할 수 있어야 신뢰가 선다.
    · **귀속은 사람이 이미 풀어 놓은 것만 쓴다.** 메신저의 대괄호 태그, 폴더 경로,
      발신 도메인. 문장에서 과제를 추측하지 않는다 — 추측은 미분류보다 나쁘다.
    · 대시보드는 `fetch` 가 아니라 `script src` 로 읽는다. 더블클릭으로 여는 파일에서
      `fetch` 는 브라우저가 막지만 `script src` 는 동작한다.
"""
from __future__ import annotations

import argparse
import datetime as dt
import glob
import html
import json
import re
import unicodedata
from pathlib import Path

PROJ = Path(__file__).resolve().parents[2]
REPO = PROJ.parents[1]
NW = REPO / "projects" / "nworks-agent" / "output"
SSOT = (Path.home() / "{{MOUNT_REL}}"
        / "공유 드라이브" / "{{SSOT_DRIVE}}")
SSOT_RPBD = SSOT / "{{DEPT_ROOT}}"
DEFS = Path(__file__).resolve().parent / "과제정의.json"
OUT = PROJ / "output" / "원장"
CLASSIFIED = PROJ / "workspace" / "재정비" / "메일분류"
INVENTORY = PROJ / "workspace" / "재정비" / "인벤토리"

WINDOW = 14          # 회차에 싣는 관측 창 (일)
ROUNDS = ("0800", "1300", "1700")

nfc = lambda s: unicodedata.normalize("NFC", s or "")          # noqa: E731
squash = lambda s: re.sub(r"\s+", " ", s or "").strip()        # noqa: E731


# ------------------------------------------------------------------ 과제 정의

def load_defs() -> list[dict]:
    d = json.loads(DEFS.read_text(encoding="utf-8"))
    for p in d["과제"]:
        p["_별칭"] = [re.sub(r"[^0-9a-z가-힣]", "", a.lower()) for a in p["별칭"]]
    return d["과제"]


def by_alias(text: str, defs: list[dict]) -> str | None:
    """문구에서 과제를 찾는다. **긴 별칭 먼저** — 짧은 것에 잡아먹히지 않게."""
    t = re.sub(r"[^0-9a-z가-힣]", "", nfc(text).lower())
    if not t:
        return None
    best = None
    for p in defs:
        for a in p["_별칭"]:
            if a and a in t and (best is None or len(a) > best[1]):
                best = (p["key"], len(a))
    return best[0] if best else None


def by_domain(email: str, defs: list[dict]) -> str | None:
    dom = (email or "").split("@")[-1].lower()
    if not dom:
        return None
    for p in defs:
        if dom in [d.lower() for d in p["도메인"]]:
            return p["key"]
    return None


# ------------------------------------------------------------------ 원천 읽기
#
# 항목 하나 = {"일자","문장","담당","출처","신뢰도","과제"}
# 신뢰도: ledger(사람이 쓴 태그·경로) · observed(관측) · unlinked(과제 미귀속)

# 기계가 보낸 메일. 분류기가 과제를 붙여 놓더라도 **소비 시점에 막는다.**
# 실측(2026-09-11): 기계 발신 38건에 과제가 붙었고 그중 37건은 kind=기계알림으로
# 정확히 판정돼 이미 걸러졌지만, 1건이 「행정」으로 새어 화면에 오를 뻔했다.
# 분류 결과 파일을 고치지 않고 읽는 쪽에서 막는 편이 되돌리기 쉽다.
MACHINE_MAIL = re.compile(
    r"(confluence@|no-?reply|noreply|donotreply|notification|drive-shares|"
    r"@slack\.com|team@mail\.|mailer-daemon|accounts\.google|@email\.)", re.I)

BUNDLE = re.compile(r"^\s*(?:각\s*)?과제별\s*진행\s*(?:상황|사항)")
TAG = re.compile(r"\[([^\]]{2,30})\]")
BOT_RELAY = re.compile(r"🔔\s*운영\s*OS")


def from_messenger(defs, since: dt.date) -> tuple[list[dict], list[dict]]:
    """부서방·AX_TF. **봇이 있는 두 방만** 읽는다 (Roy 확정 2026-09-11).

    묶음 보고(「과제별 진행 상황 공유」)는 **태그 단위로 쪼갠다.** 통째로 140자를
    잘라 첫 태그 하나에 귀속시키면 여섯 과제 정보가 한 과제 한 줄이 된다.
    """
    items, unknown = [], []
    for room in ("dept", "AX_TF"):
        for f in sorted((NW / "raw_messenger" / room).glob("*.jsonl")):
            for line in f.read_text(encoding="utf-8").splitlines():
                if not line.strip():
                    continue
                m = json.loads(line)
                ts = str(m.get("kst") or m.get("time") or "")[:19]
                day = ts[:10]
                if not day or day < since.isoformat():
                    continue
                text = nfc(m.get("text") or "")
                if not text or BOT_RELAY.search(text):
                    continue          # 봇 알림을 사람이 옮겨 붙인 것은 새 일이 아니다
                who = m.get("sender") or ""
                room_label = "부서방" if room == "dept" else "AX_TF"

                if BUNDLE.match(text) and TAG.search(text):
                    for key, body in split_bundle(text, defs):
                        for line2 in body:
                            (items if key else unknown).append({
                                "일자": day, "문장": line2, "담당": who,
                                "출처": f"{room_label} · {who}", "신뢰도": "ledger",
                                "과제": key, "원문": text[:200]})
                    continue

                if len(text) < 18:
                    continue          # 맞장구는 일이 아니다
                key = by_alias(text, defs)
                rec = {"일자": day, "문장": squash(text)[:180], "담당": who,
                       "출처": f"{room_label} · {who}",
                       "신뢰도": "ledger" if TAG.search(text) else "observed",
                       "과제": key, "원문": text[:200]}
                (items if key else unknown).append(rec)
    return items, unknown


def split_bundle(text: str, defs) -> list[tuple[str | None, list[str]]]:
    """묶음 보고를 `[태그]` 단위로 쪼갠다. 태그 뒤의 줄들이 그 과제의 항목이다."""
    parts, cur, buf = [], None, []
    for raw in re.split(r"[\n\r]+", text):
        line = squash(raw)
        if not line or line.startswith(("과제별", "각 과제별", "___", "--")):
            continue
        t = TAG.match(line)
        if t:
            if cur is not None or buf:
                parts.append((cur, buf))
            cur, buf = by_alias(t.group(1), defs), []
            rest = squash(line[t.end():])
            if rest:
                buf.append(rest)
            continue
        line = re.sub(r"^[\-·•*]\s*|^\d+\.\s*", "", line)
        if line:
            buf.append(line[:180])
    if cur is not None or buf:
        parts.append((cur, buf))
    return [(k, b) for k, b in parts if b]


def strip_html(x: str) -> str:
    x = re.sub(r"(?is)<(script|style).*?</\\1>", " ", x)
    x = re.sub(r"(?s)<[^>]+>", " ", x)
    return re.sub(r"\\s+", " ", html.unescape(x)).strip()


def read_mail(since: dt.date):
    """살아 있는 메일함을 직접 읽는다.

    예전에는 `_입력_메일전체.jsonl` 을 읽었다. 그것은 분류 배치를 돌리려고
    2026-09-11 에 **한 번 떠낸 사진**이었는데, 원장이 계속 그것만 보는 바람에
    9/12~9/21 열흘치 메일이 한 건도 안 실렸다. 그동안 화면의 「메일」 카드는
    수집기 시각을 보여 주고 있어서 **최신으로 보였다.** 낡아 보이는 것보다 위험하다.

    이제 수집기가 쌓는 건별 파일을 직접 읽는다. 사진이 아니라 창이다.
    """
    root = NW / "raw_mail" / "{{BOT_USER}}"
    if not root.is_dir():
        return
    months, cur = set(), since
    while cur <= dt.date.today():          # 창이 달을 넘어가도 빠뜨리지 않는다
        months.add(cur.strftime("%Y-%m"))
        cur += dt.timedelta(days=1)
    months.add(dt.date.today().strftime("%Y-%m"))
    for mon in sorted(months):
        d = root / mon
        if not d.is_dir():
            continue
        for p in d.glob("*.json"):
            try:
                m = (json.loads(p.read_text(encoding="utf-8")) or {}).get("mail") or {}
            except (ValueError, OSError):
                continue
            day = (m.get("receivedTime") or m.get("sentTime") or "")[:10]
            if not day or day < since.isoformat():
                continue
            frm = m.get("from") or {}
            yield {"id": f"{{BOT_USER}}/{m.get('mailId')}", "date": day,
                   "subject": m.get("subject") or "",
                   "from_email": frm.get("email") or "",
                   "from_name": frm.get("name") or "",
                   "body": strip_html(m.get("body") or "")}


def from_mail(defs, since: dt.date) -> tuple[list[dict], list[dict]]:
    """메일. 분류 결과가 있으면 그것을 쓰고, 없으면 도메인·별칭으로만 귀속한다."""
    verdict = {}
    for f in sorted(CLASSIFIED.glob("분류결과_*.jsonl")):
        for line in f.read_text(encoding="utf-8").splitlines():
            if line.strip():
                try:
                    r = json.loads(line)
                    verdict[r.get("id")] = r
                except ValueError:
                    pass

    items, unknown = [], []
    for m in read_mail(since):
        v = verdict.get(m["id"]) or {}
        if v.get("kind") in ("기계알림", "무관") or MACHINE_MAIL.search(m.get("from_email") or ""):
            continue
        # **제목이 도메인보다 먼저다.** 사람이 제목에 과제를 썼으면 그것이 정본이고,
        # 도메인은 추론이다. 연세대(yuhs.ac) 발신의 「AX-Sprint 중간평가 안내」가
        # 자폐 과제로 귀속되던 것이 순서를 뒤집었던 탓이다 (2026-09-11 실측).
        key = v.get("proj") or by_alias(m.get("subject", ""), defs) \
            or by_domain(m.get("from_email", ""), defs)
        direction = "발신" if "{{ORG_DOMAIN}}" in (m.get("from_email") or "") else "수신"
        rec = {"일자": m["date"], "문장": squash(m.get("subject"))[:160],
               "담당": m.get("from_name") or "", "출처": f"메일 {direction}",
               "신뢰도": "ledger" if v.get("conf") == "high" else "observed",
               "과제": key, "원문": (m.get("body") or "")[:200],
               "종류": v.get("kind"), "근거": v.get("why")}
        (items if key else unknown).append(rec)
    return items, unknown


# 메일 스레드 접기 ─────────────────────────────────────────────
# 같은 건의 답장·전달이 「RE:」「Fwd:」만 달리한 채 서너 줄로 늘어서면
# 보드가 「단순 나열」로 보인다. 한 줄로 접되 **몇 번 오갔는지는 남긴다** —
# 세 번 오간 건과 한 번에 끝난 건은 다른 상태이기 때문이다.
# 「FW: [RE]RE: [FW]FW: FW: …」처럼 대괄호만 두른 것도 접두어다. 콜론이 없다.
THREAD_PFX = re.compile(
    r"^\s*(?:"
    r"[\[(]\s*(?:re|fw|fwd|답장|회신|답신|전달|재전송)\s*\d*\s*[\])]"      # [RE]  [FW]  — 콜론 없음
    r"|(?:re|fw|fwd|답장|회신|답신|전달|재전송)\s*\d*\s*[:：]"                 # RE:  Fwd:  답장:
    r")\s*", re.I)


# 업무 신호 ─────────────────────────────────────────────────
# 방에 올라온 글 중 무엇이 「일」이 아닌가.
#
# **업무를 열거하지 않는다.** 업무의 종류는 끝이 없어서 열거하면 반드시 빠뜨린다.
# 실제로 「회수금 납부 독촉」과 「사업비로 지출」이 업무 목록에 없다는 이유로
# 걸러졌다. 그래서 반대로 **잡담 쪽을 좁게 지목하고 나머지는 전부 업무로 둔다.**
# 잡담이 표에 한 줄 섞이는 것은 가볍고, 업무가 사라지는 것은 무겁다.
#
# 메일·산출물·U·캘린더는 제목이나 파일 자체가 이미 일이라 검사하지 않는다.
# 가린 것을 지우지 않는다. 원장에 그대로 남고 `업무: false` 만 붙는다.
# 무조건 잡담. 길이와 무관하다
CHAT_HARD = re.compile(r"^\s*(?:@\s*Urban\s*AX\s*Bot\b|https?://\S+\s*$)", re.I)

# 짧을 때만 잡담. 「식사」「퇴근」 같은 말은 긴 업무 보고 안에도 섞여 나온다.
# 실제로 「내일 국정센터 들어가고 하성지 박사와 미팅 후 … 퇴근하겠습니다」가
# 「퇴근」 한 낱말 때문에 걸러졌다. 잡담은 짧다는 성질로 가른다.
CHAT_SOFT = re.compile(
    r"식사|점심|저녁\s*(?:먹|같이|드)|커피|출근인|퇴근|어디\s*계|자리에\s*(?:계|있)|"
    r"층에\s*있|이따\s*(?:나갈|들어)|안녕하세|수고하셨|고생하셨|축하|생일")
CHAT_MAXLEN = 45


def mark_work(items: list[dict]) -> list[dict]:
    for it in items:
        room = str(it.get("출처", "")).startswith(("부서방", "AX_TF"))
        t = (it.get("문장") or "").strip()
        chat = room and (bool(CHAT_HARD.search(t))
                         or (len(t) <= CHAT_MAXLEN and bool(CHAT_SOFT.search(t))))
        it["업무"] = not chat
    return items


def strip_thread_pfx(subject: str) -> str:
    """「FW: Re: [RE]RE: …」처럼 겹쳐 붙은 접두어를 **모두** 벗긴다."""
    t = (subject or "").strip()
    for _ in range(8):
        t2 = THREAD_PFX.sub("", t)
        if t2 == t:
            break
        t = t2
    return t.strip()


def thread_root(subject: str) -> str:
    return re.sub(r"[^0-9a-z가-힣]", "", strip_thread_pfx(subject).lower())


def fold_threads(items: list[dict]) -> list[dict]:
    """메일만 접는다. 메신저·산출물·캘린더는 건건이 다른 사건이라 그대로 둔다."""
    groups: dict[tuple, list[dict]] = {}
    out = []
    for it in items:
        if not str(it.get("출처", "")).startswith("메일"):
            out.append(it)
            continue
        root = thread_root(it.get("문장", ""))
        if len(root) < 6:                    # 제목이 너무 짧으면 묶지 않는다
            out.append(it)
            continue
        groups.setdefault((it.get("과제"), root), []).append(it)

    for _, g in groups.items():
        g.sort(key=lambda x: x["일자"], reverse=True)
        head = dict(g[0])
        # 접두어는 스레드가 하나여도 벗긴다. 규칙이 하나여야 설명할 수 있고,
        # 수신·발신은 「출처」가 이미 말한다.
        head["문장"] = strip_thread_pfx(head["문장"]) or head["문장"]
        if len(g) > 1:
            ways = {str(x.get("출처", "")).replace("메일 ", "") for x in g}
            head["출처"] = "메일 왕복" if len(ways) > 1 else g[0]["출처"]
            head["문장"] = f"{head['문장']} · 스레드 {len(g)}건 ({g[-1]['일자'][5:]}~{g[0]['일자'][5:]})"
            if any(x.get("신뢰도") == "ledger" for x in g):
                head["신뢰도"] = "ledger"
        out.append(head)
    return out


def from_ssot(defs, since: dt.date) -> list[dict]:
    """SSOT 과제 폴더 산출물. 경로가 곧 과제다 — 추측이 필요 없다."""
    items = []
    base = SSOT_RPBD / "10_과제"
    if not base.is_dir():
        return items
    groups: dict[tuple, dict] = {}
    for p in defs:
        if not p["ssot"] or not p["ssot"].startswith("10_과제"):
            continue
        folder = SSOT / "{{DEPT_ROOT}}" / p["ssot"]
        if not folder.is_dir():
            continue
        for f in folder.rglob("*"):
            try:
                if not f.is_file() or f.name.startswith((".", "_")):
                    continue
                d = dt.date.fromtimestamp(f.stat().st_mtime)
            except OSError:
                continue          # 클라우드 마운트는 간헐적으로 잠긴다
            if d < since:
                continue
            g = groups.setdefault((p["key"], d.isoformat()), {"n": 0, "names": []})
            g["n"] += 1
            if len(g["names"]) < 3:
                g["names"].append(nfc(f.name))
    for (key, day), g in groups.items():
        more = f" 외 {g['n'] - 3}건" if g["n"] > 3 else ""
        items.append({"일자": day, "문장": f"산출물 {g['n']}건 — {', '.join(g['names'])}{more}",
                      "담당": "", "출처": "SSOT 산출물", "신뢰도": "observed",
                      "과제": key, "원문": ""})
    return items


def from_u(defs, since: dt.date) -> list[dict]:
    """U 드라이브. 인벤토리 TSV(Drive API 전수)에서 최근 변경을 읽는다.

    개별 파일을 한 줄로 세지 않는다 — 파일 목록이 되어 버린다.
    (과제 × 날짜 × 건수)로 묶어 「얼마나 움직였나」까지만 말한다.
    """
    items = []
    for p in defs:
        # **최근 갱신분을 먼저 본다.** 전수 인벤토리는 한 번 찍은 스냅샷이라
        # 그것만 쓰면 오늘 만든 파일이 영영 보이지 않는다 (2026-09-14 실측: 사흘 공백).
        f = INVENTORY / "최근" / f"{p['key']}.tsv"
        if not f.exists() or not f.stat().st_size:
            f = INVENTORY / f"{p['key']}.tsv"
        if not f.exists():
            continue
        groups: dict[str, dict] = {}
        for line in f.read_text(encoding="utf-8").splitlines():
            c = line.split("\t")
            if len(c) < 3:
                continue
            day = c[0][:10]
            if day < since.isoformat():
                continue
            name = nfc(c[2].split("/")[-1])
            g = groups.setdefault(day, {"n": 0, "names": []})
            g["n"] += 1
            if len(g["names"]) < 3:
                g["names"].append(name)
        for day, g in groups.items():
            more = f" 외 {g['n'] - 3}건" if g["n"] > 3 else ""
            items.append({"일자": day, "문장": f"U 드라이브 {g['n']}건 — {', '.join(g['names'])}{more}",
                          "담당": "", "출처": "U 드라이브", "신뢰도": "observed",
                          "과제": p["key"], "원문": ""})
    return items


CAL_KW = re.compile(r"마감|제출|심의|심사|실사|접수|평가|점검|보고회|발표|종료|만료")


def from_calendar(defs, today: dt.date, horizon: int = 45) -> list[dict]:
    items, seen = [], set()
    root = NW / "raw_calendar"
    if not root.is_dir():
        return items
    limit = today + dt.timedelta(days=horizon)
    for f in root.glob("*/*.json"):
        try:
            d = json.loads(f.read_text(encoding="utf-8"))
        except (ValueError, OSError):
            continue
        for ev in d.get("events", []):
            for c in ev.get("eventComponents", []):
                title = squash(c.get("summary"))
                if not title or not CAL_KW.search(title):
                    continue
                s = c.get("start") or {}
                day = (s.get("dateTime") or s.get("date") or "")[:10]
                try:
                    dd = dt.date.fromisoformat(day)
                except ValueError:
                    continue
                if not (today <= dd <= limit) or (title, day) in seen:
                    continue
                seen.add((title, day))
                items.append({"일자": day, "문장": title,
                              "담당": (c.get("organizer") or {}).get("displayName") or "",
                              "출처": f"캘린더 {f.parent.name}", "신뢰도": "observed",
                              "과제": by_alias(title, defs), "원문": ""})
    return items


# ------------------------------------------------------------------ 신선도

def newest(paths) -> str | None:
    best = None
    for p in paths:
        try:
            t = dt.datetime.fromtimestamp(p.stat().st_mtime)
        except OSError:
            continue
        if best is None or t > best:
            best = t
    return best.strftime("%Y-%m-%d %H:%M") if best else None


def newest_mail() -> str | None:
    """실제로 들어온 가장 최근 메일의 시각. 수집기가 돈 시각이 아니다."""
    root = NW / "raw_mail" / "{{BOT_USER}}"
    best = None
    for d in sorted(root.glob("[0-9][0-9][0-9][0-9]-[0-9][0-9]"), reverse=True)[:2]:
        for p in d.glob("*.json"):
            try:
                m = (json.loads(p.read_text(encoding="utf-8")) or {}).get("mail") or {}
            except (ValueError, OSError):
                continue
            t = (m.get("receivedTime") or m.get("sentTime") or "")[:16].replace("T", " ")
            if t and (best is None or t > best):
                best = t
        if best:
            break
    return best


def freshness() -> dict:
    """원천별로 언제까지 반영됐나. **하나의 시각이 여덟 스트림을 대표하면 화면이 거짓말을 한다.**

    각 값에 주기와 한 줄 설명을 붙여 화면이 그대로 쓸 수 있게 한다.
    """
    wk = REPO / "projects" / "urban-weekly-dashboard" / "data" / "parsed"
    fa = REPO / "projects" / "fathom-agent" / "data" / "actions.json"
    bg = REPO / "projects" / "rnd-budget-agent" / "data" / "dataset"
    return {
        # **수집기가 언제 돌았나가 아니라 가장 최근 메일이 언제 것인가**를 적는다.
        # 수집기 시각을 적었더니 원장이 9/11 사진에 묶여 있는 동안에도
        # 카드가 「오늘」로 떠서 열흘간 아무도 의심하지 않았다.
        "메일": {"t": newest_mail(),
                "주기": "1시간", "설명": "부서 전원·부서 그룹 · 수신 발신 모두"},
        "캘린더": {"t": newest((NW / "raw_calendar").glob("*/*.json")),
                 "주기": "1시간", "설명": "4인"},
        "부서방": {"t": newest((NW / "raw_messenger" / "dept").glob("*.jsonl")),
                 "주기": "콜백 실시간", "설명": "봇이 있는 방"},
        "AX_TF": {"t": newest((NW / "raw_messenger" / "AX_TF").glob("*.jsonl")),
                  "주기": "콜백 실시간", "설명": "봇이 있는 방"},
        "SSOT 산출물": {"t": newest([SSOT / "_시스템" / "상태" / "canary.json"]),
                     "주기": "3시간", "설명": "과제 폴더 · 색인"},
        "주간보고": {"t": newest(wk.glob("*.json")) if wk.is_dir() else None,
                  "주기": "화 09:30", "설명": "컨플루언스"},
        # 회의록은 Fathom·녹음을 로컬 전사해 만드는 구조이고 **아직 설계 중이다**
        # (Roy 확인 2026-09-14, 별도 에이전트와 진행). 고장 난 파이프라인이 아니므로
        # 낡은 날짜를 띄우지 않는다. 완성되면 과제 폴더로 이관돼 SSOT 산출물로 붙는다.
        "회의록": {"t": None, "주기": "설계 중",
                 "설명": "Fathom·녹음 → 로컬 전사 → 과제 폴더 이관 (별도 진행)"},
        "예산 원장": {"t": newest(bg.glob("budget_*.json")) if bg.is_dir() else None,
                   "주기": "3시간 사이클", "설명": "U 2-7 엑셀"},
        "U 드라이브": {"t": newest(list((INVENTORY / "최근").glob("*.tsv"))
                                  or list(INVENTORY.glob("*.tsv"))),
                    "주기": "회차마다", "설명": "과제 폴더 최근 21일 · 원장 연동"},
    }


# ------------------------------------------------------------------ 회차 생성

def round_of(now: dt.datetime) -> str:
    """지금이 어느 회차인가. 08·13·17 중 가장 가까운 지난 것."""
    h = now.hour
    return "1700" if h >= 17 else "1300" if h >= 13 else "0800"


def prev_rounds(now: dt.datetime, back: int = 10) -> list[Path]:
    out = []
    for i in range(back * 3):
        d = now.date() - dt.timedelta(days=i // 3)
        r = ROUNDS[::-1][i % 3]
        p = OUT / "이력" / f"{d:%Y-%m}" / f"{d:%Y-%m-%d}_{r}.md"
        if p.exists():
            out.append(p)
    return out


def missing_rounds(now: dt.datetime, days: int = 3) -> list[str]:
    """놓친 회차. 맥이 꺼져 있었으면 파일이 없다 — 감추지 않고 적는다."""
    miss = []
    cur = f"{now:%Y-%m-%d}_{round_of(now)}"
    for i in range(days):
        d = now.date() - dt.timedelta(days=i)
        if d.weekday() >= 5:
            continue                      # 주말은 회차를 돌리지 않는다
        for r in ROUNDS:
            tag = f"{d:%Y-%m-%d}_{r}"
            if tag >= cur:
                continue
            if not (OUT / "이력" / f"{d:%Y-%m}" / f"{tag}.md").exists():
                miss.append(tag)
    return sorted(miss)


def sig(it: dict) -> str:
    return f"{it['과제']}|{it['일자']}|{re.sub(r'[^0-9가-힣A-Za-z]', '', it['문장'])[:60]}"


def load_prev(now: dt.datetime) -> tuple[str | None, set]:
    for p in prev_rounds(now):
        m = re.search(r"<!--SIG\n(.*?)\nSIG-->", p.read_text(encoding="utf-8"), re.S)
        if m:
            return p.stem, set(m.group(1).split("\n"))
    return None, set()


def render(defs, items, unknown, fresh, now, rnd, prev_name, added, dropped, miss) -> str:
    by_key: dict[str, list] = {}
    for it in items:
        by_key.setdefault(it["과제"], []).append(it)

    L = []
    L.append("---")
    L.append(f"생성: {now:%Y-%m-%dT%H:%M}+09:00")
    L.append(f"회차: {now:%Y-%m-%d}_{rnd}")
    L.append(f"직전: {prev_name or '없음'}")
    L.append(f"창: 최근 {WINDOW}일")
    L.append("원천:")
    for k, v in fresh.items():
        L.append(f"  {k}: {v.get('t') or '읽기 실패'}  ({v.get('주기')} · {v.get('설명')})")
    L.append(f"결손: [{', '.join(miss) if miss else ''}]")
    L.append(f"항목: {len(items)}  미분류: {len(unknown)}")
    L.append("---\n")

    L.append(f"# 운영 OS 데이터 원장 · {now:%Y-%m-%d} {rnd[:2]}:{rnd[2:]}\n")

    L.append("## 직전 회차 대비 변경\n")
    if not prev_name:
        L.append("_직전 회차가 없습니다. 이번이 첫 회차입니다._\n")
    elif not added and not dropped:
        L.append("_변경 없음._\n")
    else:
        for it in added[:60]:
            p = next((x for x in defs if x["key"] == it["과제"]), None)
            L.append(f"- **신규** [{p['이름'] if p else it['과제']}] {it['문장']} "
                     f"`{it['출처']} · {it['일자']}`")
        if len(added) > 60:
            L.append(f"- _신규 {len(added)}건 중 60건만 표시_")
        if dropped:
            L.append(f"- _창에서 빠진 항목 {len(dropped)}건 (14일 경과)_")
        L.append("")

    if miss:
        L.append("## 결손\n")
        L.append(f"놓친 회차 {len(miss)}건: {', '.join(miss)}. "
                 "그 사이의 변경은 이번 회차에 한꺼번에 들어 있습니다.\n")

    L.append("## 과제별\n")
    for p in defs:
        rows = sorted(by_key.get(p["key"], []), key=lambda x: x["일자"], reverse=True)
        L.append(f"### {p['no']}. {p['이름']}")
        L.append(f"`{p['구분']}` · `{p['key']}`" + (f" · `{p['pid']}`" if p["pid"] else "")
                 + f" · 항목 {len(rows)}건")
        if not rows:
            L.append("\n_관측된 항목이 없습니다._\n")
            continue
        L.append("")
        for it in rows[:25]:
            mark = "**" if it["신뢰도"] == "ledger" else ""
            L.append(f"- [{it['일자'][5:]}] {mark}{it['문장']}{mark} `{it['출처']}`")
        if len(rows) > 25:
            L.append(f"- _외 {len(rows) - 25}건_")
        L.append("")

    L.append("## 미분류\n")
    if not unknown:
        L.append("_없습니다._\n")
    else:
        L.append(f"어느 과제에도 귀속되지 않은 관측 **{len(unknown)}건**입니다. "
                 "추측해서 붙이지 않았습니다. AX_TF 방에 분류를 요청할 대상입니다.\n")
        for it in sorted(unknown, key=lambda x: x["일자"], reverse=True)[:40]:
            L.append(f"- [{it['일자'][5:]}] {it['문장'][:120]} `{it['출처']}`")
        if len(unknown) > 40:
            L.append(f"- _외 {len(unknown) - 40}건_")
        L.append("")

    L.append("<!--SIG")
    L.append("\n".join(sorted(sig(it) for it in items)))
    L.append("SIG-->")
    return "\n".join(L)


def to_js(defs, items, unknown, fresh, now, rnd, miss) -> str:
    by_key: dict[str, list] = {}
    for it in items:
        by_key.setdefault(it["과제"], []).append(it)
    data = {
        "생성": f"{now:%Y-%m-%dT%H:%M}+09:00",
        "회차": f"{now:%Y-%m-%d}_{rnd}",
        "원천": fresh, "결손": miss,
        "과제": [{
            "no": p["no"], "key": p["key"], "구분": p["구분"], "이름": p["이름"], "pid": p["pid"],
            # 별칭을 함께 싣는다. 화면이 자기 표의 과제명과 원장을 **이름으로** 이어야 하기 때문이다.
            # 행 순서로 이으면 표에서 한 줄만 빠져도 그 아래가 전부 밀려 엉뚱한 과제에 붙는다.
            "별칭": p["별칭"],
            "항목": sorted(by_key.get(p["key"], []), key=lambda x: x["일자"], reverse=True)[:40],
        } for p in defs],
        "미분류": sorted(unknown, key=lambda x: x["일자"], reverse=True)[:100],
    }
    return ("/* 운영 OS 데이터 원장 — 기계 생성. 손으로 고치지 마십시오. */\n"
            "var OPS_DATA = " + json.dumps(data, ensure_ascii=False, indent=1) + ";\n")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--회차", dest="rnd", choices=ROUNDS)
    ap.add_argument("--dry", action="store_true")
    a = ap.parse_args()

    now = dt.datetime.now()
    rnd = a.rnd or round_of(now)
    since = now.date() - dt.timedelta(days=WINDOW)
    defs = load_defs()

    items, unknown = [], []
    mi, mu = from_messenger(defs, since); items += mi; unknown += mu
    ei, eu = from_mail(defs, since);      items += ei; unknown += eu
    items += from_ssot(defs, since)
    items += from_u(defs, since)
    items += from_calendar(defs, now.date())
    items = [it for it in items if it["과제"]]
    items = fold_threads(items)
    unknown = fold_threads(unknown)   # 미분류도 접는다. 같은 스레드를 두 번 묻지 않는다
    items = mark_work(items)

    fresh = freshness()
    miss = missing_rounds(now)
    prev_name, prev_sig = load_prev(now)
    cur_sig = {sig(it) for it in items}
    added = [it for it in items if sig(it) not in prev_sig]
    dropped = prev_sig - cur_sig

    md = render(defs, items, unknown, fresh, now, rnd, prev_name, added, dropped, miss)
    js = to_js(defs, items, unknown, fresh, now, rnd, miss)

    print(f"· 회차 {now:%Y-%m-%d}_{rnd} · 항목 {len(items)} · 미분류 {len(unknown)} "
          f"· 신규 {len(added)} · 결손 {len(miss)}")
    for p in defs:
        n = sum(1 for it in items if it["과제"] == p["key"])
        print(f"   {p['no']:2}. {p['이름'][:34]:34} {n:3}건")
    if a.dry:
        print("  (dry — 파일을 쓰지 않았습니다)")
        return 0

    hist = OUT / "이력" / f"{now:%Y-%m}"
    hist.mkdir(parents=True, exist_ok=True)
    (hist / f"{now:%Y-%m-%d}_{rnd}.md").write_text(md, encoding="utf-8")
    (OUT / "최신.md").write_text(md, encoding="utf-8")
    (OUT / "최신.js").write_text(js, encoding="utf-8")
    print(f"  → {(hist / f'{now:%Y-%m-%d}_{rnd}.md').relative_to(PROJ)}")
    print(f"  → output/원장/최신.md · 최신.js")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
