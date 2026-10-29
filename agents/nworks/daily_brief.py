"""부서방 정기 브리핑 — 평일(주말·공휴일 제외) 08:00 / 17:00 발송.

운영자 승인 발송 유형 (2026-08-05):
- 아침(08:00): 오늘 일정(담당자별) + 어제(월요일이면 주말 포함) 수신 부서 메일({{BOT_USER}}) 서머리
- 저녁(17:00): 다음 영업일 일정(담당자별) + 오늘 수신 부서 메일({{BOT_USER}}) 서머리

동작: 공휴일/주말이면 침묵 종료 → 수집 사이클 선실행(최신화) → 브리핑 구성 →
     부서방(dept_channel_id)으로 발송 (2,000자 한도 분할)
메일 서머리는 hermes 원샷(3분 제한) — 실패 시 제목 목록으로 폴백.
launchd: com.{{ORG_NAME}}.nworks-brief (모드는 실행 시각으로 자동 판별, 인자로 강제 가능)
"""
import datetime
import json
import re
import subprocess
import sys
import time
from pathlib import Path

import holidays

PROJ = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJ / "agents"))
from nworks.client import NworksClient  # noqa: E402
from nworks.config import load_config  # noqa: E402
from nworks.domains import bot_send_text  # noqa: E402

LOG = PROJ / "workspace" / "daily_brief.log"
KR_HOLIDAYS = holidays.KR(years=range(2026, 2028))
WEEKDAY_KO = "월화수목금토일"



def _identity():
    """신원 해석기 — 사람 목록·ID 는 설정과 조직도에서 읽는다 (코드에 박지 않는다)."""
    import sys as _s
    from pathlib import Path as _P
    _s.path.insert(0, str(_P(__file__).resolve().parents[2] / "core"))
    from lib import identity                                     # noqa: PLC0415
    return identity


def _zero_label() -> str:
    """메일 0건의 두 원인을 구별한다 — 보완기획 §5 ⑤.

    '수신 0건'(정상)과 '수집 0건'(파이프라인 정지)은 브리핑에서 똑같이 보였다.
    22일간 매일 "발송 완료"가 찍히는 동안 소스가 8/12에 멈춰 있던 사고의 표시면이다.
    하트비트가 낡았으면 수집 정지로 표기한다.
    """
    try:
        import sys as _s
        from pathlib import Path as _P
        _s.path.insert(0, str(_P.home() / "{{WORKSPACE_NAME}}" / "scripts"))
        from lib import hb as _hb
        d = _hb.read("nworks-mail")
        if d is None:
            return "수신 메일 없음 (⚠ 수집 상태 확인 불가 — 하트비트 없음)"
        age, cap = _hb.age_min(d), d.get("expected_max_age_min", 90)
        if d.get("status") != "ok" or (age is not None and age > cap):
            return (f"⚠ **메일 수집이 멈춰 있습니다** — 마지막 성공 {age:.0f}분 전"
                    f" (허용 {cap}분). 아래 목록은 그 시점 기준입니다.")
        return "수신 메일 없음"
    except Exception:
        return "수신 메일 없음"

def log(msg: str) -> None:
    LOG.parent.mkdir(exist_ok=True)
    with LOG.open("a") as f:
        f.write(f"[{datetime.datetime.now():%Y-%m-%d %H:%M:%S}] {msg}\n")


def is_business_day(d: datetime.date) -> bool:
    return d.weekday() < 5 and d not in KR_HOLIDAYS


def next_business_day(d: datetime.date) -> datetime.date:
    n = d + datetime.timedelta(days=1)
    while not is_business_day(n):
        n += datetime.timedelta(days=1)
    return n


def prev_business_day(d: datetime.date) -> datetime.date:
    p = d - datetime.timedelta(days=1)
    while not is_business_day(p):
        p -= datetime.timedelta(days=1)
    return p


def _targets() -> list:
    return _identity().targets("calendar")


def calendar_for_day(day: datetime.date) -> dict:
    """{담당자명: [(HH:MM, 제목)]} — raw_calendar 개인별 월 파일에서 해당 일자 추출."""
    out = {}
    ds = day.isoformat()
    ym = ds[:7]
    for t in _targets():
        local = t["email"].split("@")[0]
        f = PROJ / "output" / "raw_calendar" / local / f"{ym}.json"
        if not f.exists():
            continue
        items = []
        for e in json.loads(f.read_text()).get("events", []):
            for comp in e.get("eventComponents") or []:
                start = comp.get("start", {})
                when = start.get("dateTime") or start.get("date") or ""
                if when[:10] == ds:
                    items.append((when[11:16] or "종일", comp.get("summary") or "(제목 없음)"))
        seen = set()
        uniq = [x for x in sorted(items) if not (x in seen or seen.add(x))]
        if uniq:
            out[t["name"]] = uniq
    return out


# 운영자 방침(2026-08-12): 브리핑은 업무 진행 내용만 — 금액·액수는 싣지 않는다.
# 통화 기호/단위가 붙은 수치만 치환한다 (날짜·전화번호 오탐 방지).
AMOUNT_RE = re.compile(
    r"(?:₩|＄|\$|USD|KRW)\s*[0-9][0-9,.]*"
    r"|[0-9][0-9,.]*\s*(?:억\s*원|천만\s*원|백만\s*원|만\s*원|천\s*원|만원|원|달러|불)")


def redact_amounts(text: str) -> str:
    return AMOUNT_RE.sub("(금액 제외)", text or "")


# 운영자 방침(2026-08-13): 시스템 자동알림·중복·테스트는 브리핑에 싣지 않는다.
# 규칙은 config/brief_filter.json 이 소유한다 — 여기 코드는 규칙을 해석만 한다.
# 수집 자체는 막지 않는다. 브리핑에서만 뺀다(삭제는 되돌릴 수 없다).
FILTER_CFG = PROJ / "config" / "brief_filter.json"


def _load_filter() -> dict:
    """판정 규칙을 읽는다. **규칙을 소유하는 파일은 이것 하나다.**

    없으면 거르지 않고 전부 통과시킨다 — 조용히 0건이 되는 것보다 낫다.
    대신 **없다는 사실과 어디서 시작하면 되는지**를 로그에 남긴다.
    """
    if not FILTER_CFG.is_file():
        example = FILTER_CFG.with_name("brief_filter.example.json")
        log(f"판정 규칙이 없다 — 거르지 않고 전부 통과시킨다. "
            f"시작하려면 {example.name} 을 {FILTER_CFG.name} 으로 복사해 채운다")
        return {}
    try:
        return json.loads(FILTER_CFG.read_text(encoding="utf-8"))
    except Exception as e:                                   # noqa: BLE001
        log(f"판정 규칙을 읽지 못했다(거르지 않고 진행): {str(e)[:100]}")
        return {}


def brief_skip_reason(subject: str, sender: str, cfg: dict) -> str | None:
    """브리핑에서 뺄 이유. 실으면 None."""
    if not cfg:
        return None
    addr = (sender or "").lower().strip()
    local, _, domain = addr.partition("@")
    subj = subject or ""

    for pat in (cfg.get("test_subject") or {}).get("patterns", []):
        if re.search(pat, subj, re.I):
            return "테스트"

    # 개인 사안은 **사내 보호보다 먼저** 본다. 급여·인사 메일은 바로 사내에서 오기 때문에
    # 아래 protected_domains 로 빠져나가면 그대로 부서방에 요약돼 나간다.
    prs = cfg.get("personal_senders") or {}
    if any(domain == d or domain.endswith("." + d) for d in prs.get("domains", [])):
        return "개인"
    for pat in (cfg.get("personal_subject") or {}).get("patterns", []):
        if re.search(pat, subj, re.I):
            return "개인"

    # 사내 발신은 테스트·개인 외에는 거르지 않는다
    if any(domain == d or domain.endswith("." + d)
           for d in cfg.get("protected_domains", [])):
        return None

    sysc = cfg.get("system_senders") or {}
    if any(p in local for p in sysc.get("local_parts", [])):
        return "시스템알림"
    if any(domain == d or domain.endswith("." + d) for d in sysc.get("domains", [])):
        return "시스템알림"

    mkt = cfg.get("marketing_senders") or {}
    if any(domain == d or domain.endswith("." + d) for d in mkt.get("domains", [])):
        return "마케팅"
    return None


def strip_personal_body(m: dict, cfg: dict) -> bool:
    """본문에만 개인 정보가 있으면 본문을 지운다. 지웠으면 True.

    메일 자체를 빼지 않는 이유 — 실제로 「과제비 집행 추가 증빙 요청」 같은 **업무 메일**이
    본문에서 급여명세를 언급한다. 통째로 빼면 업무 사안을 잃고, 그대로 두면 요약기를 거쳐
    급여 내용이 방으로 나간다. 그래서 사안(제목)은 남기고 내용만 뺀다.
    """
    pats = (cfg.get("personal_body") or {}).get("patterns", [])
    body = m.get("body") or ""
    if body and any(re.search(p, body, re.I) for p in pats):
        m["body"] = "(개인정보가 포함되어 본문 제외 — 원문 메일 확인)"
        return True
    return False


def apply_brief_filter(mails: list) -> tuple[list, dict]:
    """(실을 메일, 제외 사유별 건수). 같은 발신자·제목 반복은 1건으로 접는다."""
    cfg = _load_filter()
    dedupe = (cfg.get("dedupe") or {}).get("enabled", True)
    kept, dropped, seen = [], {}, set()
    for m in mails:
        why = brief_skip_reason(m.get("subject", ""), m.get("from", ""), cfg)
        if why:
            dropped[why] = dropped.get(why, 0) + 1
            continue
        if strip_personal_body(m, cfg):
            dropped["본문제외"] = dropped.get("본문제외", 0) + 1
        if dedupe:
            key = ((m.get("from") or "").lower(), (m.get("subject") or "").strip())
            if key in seen:
                dropped["중복"] = dropped.get("중복", 0) + 1
                continue
            seen.add(key)
        kept.append(m)
    return kept, dropped


def mails_between(frm: datetime.date, to: datetime.date) -> list:
    """[frm 00:00, to 24:00) 수신 부서 메일 — 전 계정 raw_mail에서 추출 (금액은 소스에서 제외).

    계정 트리를 전부 읽는다({{BOT_USER}} 현행 · research 는 8/13 이전 과거분). 같은 메일이 두 편지함에
    수신된 경우는 (제목, 수신시각)으로 병합 — 계정 전환기 중복 방지."""
    out = []
    seen_keys = set()
    for f in (PROJ / "output" / "raw_mail").glob("*/*/[0-9]*.json"):
        m = json.loads(f.read_text())
        mo = m.get("mail") or m
        received = (mo.get("receivedTime") or "")[:10]
        key = (mo.get("subject") or "", (mo.get("receivedTime") or "")[:16])
        if key in seen_keys:
            continue
        seen_keys.add(key)
        if received and frm.isoformat() <= received <= to.isoformat():
            out.append({
                "subject": redact_amounts(mo.get("subject") or "(제목 없음)"),
                "from": (mo.get("from") or {}).get("email", ""),
                "received": mo.get("receivedTime", "")[:16],
                "body": redact_amounts((mo.get("body") or "")[:1500]),
                "atts": len(m.get("attachments") or []),
            })
    out.sort(key=lambda x: x["received"])
    return out


RE_PREFIX = re.compile(r"^\s*(?:(?:re|fw|fwd|답장|전달)\s*[:：]\s*)+", re.I)


def thread_key(subject: str) -> str:
    """Re:/FW: 를 걷어낸 제목. 같은 사안이 3~4건씩 올라오는 것을 하나로 묶는다."""
    s = subject or ""
    while True:
        stripped = RE_PREFIX.sub("", s)
        if stripped == s:
            break
        s = stripped
    return re.sub(r"\s+", " ", s).strip().lower()


def display_subject(subject: str) -> str:
    """Re:/FW: 를 걷어낸 표시용 제목. 원문 대소문자는 유지한다."""
    s = subject or ""
    while True:
        stripped = RE_PREFIX.sub("", s)
        if stripped == s:
            return s.strip() or subject
        s = stripped


def group_threads(mails: list) -> list:
    """메일 목록 → 사안(스레드) 목록. 시간순 유지."""
    threads: dict[str, dict] = {}
    for m in mails:
        k = thread_key(m.get("subject", ""))
        t = threads.setdefault(
            k, {"subject": display_subject(m.get("subject", "")), "msgs": []})
        t["msgs"].append(m)
    return list(threads.values())


def summarize_mails(mails: list) -> str:
    if not mails:
        return _zero_label()
    threads = group_threads(mails)
    blocks = []
    for t in threads:
        head = f"사안: {t['subject']}"
        if len(t["msgs"]) > 1:
            who = " → ".join(dict.fromkeys(m["from"].split("@")[0] for m in t["msgs"]))
            head += f"\n(같은 사안 {len(t['msgs'])}건 · {who})"
        body = "\n".join(f"[{m['from']}] {m['body'][:400]}" for m in t["msgs"])
        blocks.append(f"{head}\n{body}")
    prompt = (
        "다음은 부서가 오늘 받은 메일을 사안별로 묶은 것이다. "
        "**사안 하나당 정확히 1~2줄** 한국어로 요약하라. "
        "여러 건이 묶인 사안은 오간 내용을 합쳐 결론과 남은 할 일이 드러나게 쓴다. "
        "형식: '- [핵심 인물] 내용 (요청·기한 있으면 명시)'. 서론·결론 없이 목록만. "
        "금액·액수·단가·연봉 등 돈 관련 수치는 절대 포함하지 마라.\n\n"
        + "\n---\n".join(blocks))
    try:
        r = subprocess.run(["hermes", "-z", prompt, "--safe-mode"],
                           capture_output=True, text=True, timeout=180)
        s = (r.stdout or "").strip()
        if s:
            return s
    except Exception as e:
        log(f"hermes 서머리 실패: {str(e)[:100]}")
    return "\n".join(f"- {t['subject']}"
                     + (f" (관련 {len(t['msgs'])}건)" if len(t["msgs"]) > 1 else "")
                     for t in threads)


RT_DAYS = 90       # NAVER WORKS UA refresh token lifetime (Developer Console, checked 2026-10-29)
RT_WARN_DAYS = 14  # start warning two weeks before expiry


def token_expiry_lines(today: datetime.date) -> list:
    """Countdown to NAVER WORKS mail API (UA refresh token) expiry. Mail collection stops at 0."""
    import glob
    import os
    out = []
    for path in sorted(glob.glob(os.path.expanduser("~/.nworks/ua_token_*.json"))):
        try:
            tok = json.loads(Path(path).read_text())
        except Exception:
            continue
        issued = tok.get("rt_issued_at")
        acct = Path(path).stem.replace("ua_token_", "")
        if not issued:
            out.append(f"🔑 네이버웍스 메일 API({acct}) 토큰 발급일 기록 없음 >> 만료일 확인 필요")
            continue
        exp = datetime.date.fromtimestamp(issued) + datetime.timedelta(days=RT_DAYS)
        left = (exp - today).days
        if left < 0:
            out.append(f"⛔ 네이버웍스 메일 API({acct}) 토큰 만료됨 ({exp}) >> 메일 수집 중단. 재동의 필요: nwctl ua-auth")
        elif left <= RT_WARN_DAYS:
            out.append(f"⚠️ 네이버웍스 메일 API({acct}) 토큰 만료 D-{left} ({exp}) >> 만료 전 재동의 필요: nwctl ua-auth")
        else:
            out.append(f"🔑 네이버웍스 메일 API({acct}) 토큰 만료 D-{left} ({exp}) · Rotation On: 수집이 돌 때마다 90일 자동 연장")
    return out


def mail_pipeline_line() -> str:
    """One-line health of the mail pipeline: newest collected mail and parsed count."""
    # Only the live collection account matters (frozen past series are not counted).
    accts = [d for d in (PROJ / "output" / "raw_mail").iterdir() if d.is_dir()] \
        if (PROJ / "output" / "raw_mail").exists() else []
    if not accts:
        return "📮 메일 수집 원본 0건 >> 수집 경로 점검 필요"
    live = max(accts, key=lambda d: (d / "state.json").stat().st_mtime if (d / "state.json").exists() else 0)
    raws = list(live.glob("*/[0-9]*.json"))
    if not raws:
        return f"📮 메일 수집 원본 0건({live.name}) >> 수집 경로 점검 필요"
    ts = datetime.datetime.fromtimestamp(max(f.stat().st_mtime for f in raws))
    parsed = sum(1 for _ in (PROJ / "output" / "stage" / f"mail_{live.name}").glob("*/*.md"))
    return f"📮 메일 수집 정상({live.name}) · 수집 {len(raws)}건 · 정리 {parsed}건 · 최근 수집 {ts:%m/%d %H:%M}"


def compose(mode: str, today: datetime.date) -> str:
    if mode == "morning":
        cal_day = today
        m_from = prev_business_day(today)
        m_to = today - datetime.timedelta(days=1)
        cal_label = f"오늘 {cal_day.month}/{cal_day.day}({WEEKDAY_KO[cal_day.weekday()]})"
        mail_label = ("어제" if m_from == m_to
                      else f"{m_from.month}/{m_from.day}~{m_to.month}/{m_to.day}(주말 포함)")
    else:
        cal_day = next_business_day(today)
        m_from = m_to = today
        cal_label = f"다음 영업일 {cal_day.month}/{cal_day.day}({WEEKDAY_KO[cal_day.weekday()]})"
        mail_label = "오늘"

    cal = calendar_for_day(cal_day)
    lines = [f"[{{ORG_NAME}} AX 브리핑] {cal_label} 일정"]
    if cal:
        for name, items in cal.items():
            lines.append(f"\n▷ {name}")
            for t, s in items:
                lines.append(f"  {t}  {s}")
    else:
        lines.append("등록된 일정 없음")

    mails, dropped = apply_brief_filter(mails_between(m_from, m_to))
    if dropped:
        log("브리핑 제외: " + ", ".join(f"{k} {v}" for k, v in sorted(dropped.items())))
    n_thread = len(group_threads(mails))
    count = (f"{len(mails)}건" if n_thread == len(mails)
             else f"{n_thread}개 사안 · 메일 {len(mails)}건")
    lines.append(f"\n📧 {mail_label} 부서 수신 메일 ({count})")
    lines.append(summarize_mails(mails))
    lines.append("")
    lines.append(mail_pipeline_line())
    lines.extend(token_expiry_lines(today))
    return "\n".join(lines)


def send_chunks(text: str) -> None:
    cfg = load_config()
    client = NworksClient()
    chunks, cur = [], ""
    for line in text.split("\n"):
        if len(cur) + len(line) + 1 > 1900:
            chunks.append(cur)
            cur = line
        else:
            cur = f"{cur}\n{line}" if cur else line
    if cur:
        chunks.append(cur)
    for i, c in enumerate(chunks):
        head = f"({i+1}/{len(chunks)})\n" if len(chunks) > 1 else ""
        bot_send_text(client, cfg["bot_id"], head + c, channel_id=cfg["dept_channel_id"])


def main() -> None:
    now = datetime.datetime.now()
    today = now.date()
    mode = sys.argv[1] if len(sys.argv) > 1 else ("morning" if now.hour < 12 else "evening")
    if mode not in ("morning", "evening"):
        mode = "morning"
    if not is_business_day(today):
        log(f"skip: {today} 휴일/주말")
        return
    # 최신 데이터 확보 — 수집 사이클 선실행
    try:
        import nworks_cycle
        nworks_cycle.main()
    except Exception as e:
        log(f"사전 수집 실패(기존 데이터로 진행): {str(e)[:150]}")
    # 재시도 정책(2026-08-12): 일시 장애(DNS 등)로 회차가 통째로 사라지는 것을 막는다.
    # 10분 간격 5회 — 08:00 회차가 08:50에라도 나가는 것이 소실보다 낫다.
    last_err = None
    for attempt in range(1, 6):
        try:
            text = compose(mode, today)
            send_chunks(text)
            log(f"{mode} 브리핑 발송 완료 ({len(text)}자, {attempt}차 시도)")
            return
        except Exception as e:
            last_err = e
            log(f"{mode} 브리핑 {attempt}/5차 실패: {str(e)[:150]}")
            if attempt < 5:
                time.sleep(600)
    log(f"{mode} 브리핑 최종 실패 — 회차 소실: {str(last_err)[:200]}")
    try:  # 경고 발송도 같은 장애로 실패할 수 있음 — 그 경우 로그가 유일한 흔적
        cfg = load_config()
        bot_send_text(NworksClient(), cfg["bot_id"],
                      f"⚠️ {mode} 브리핑 5회 재시도 후 실패 — daily_brief.log 확인",
                      user_id="{{OPERATOR_ACCOUNT}}")
    except Exception:
        pass


if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).parent))
    main()
