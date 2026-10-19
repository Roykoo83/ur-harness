#!/usr/bin/env python3
"""주간보고 초안 — 원장·메일·메신저 증거 기반, 부서 공식 양식 그대로.

    python3 scripts/tasks/weekly_draft.py

칸의 의미 (운영자 정정 2026-08-31):
    전주 실적 = **직전 보고서의 「금주 계획」 중 실제 수행된 것** + 추가로 수행된 것.
               수행 여부는 원장 완료 기록·메신저·메일의 완료 신호로 판정한다.
    금주 계획 = 보고일(화)부터 차주 월요일까지의 일: 기한이 이 창에 있는 태스크
               + 지난주 계획 중 못 끝난 것(지속) + 지난주에 새로 생긴 열린 요청.
    이슈     = 막힘·확인 필요.

지난주 것을 금주로 밀어 넣지 않는다. 같은 사안은 칸 안에서 한 번만 쓴다.
산출: data/tasks/weekly_draft.json  (inject §07 · make_weekly_pptx 가 읽는다)
"""
from __future__ import annotations

import datetime as dt
import json
import re
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import extract as E            # noqa: E402
import extract_observed as EO  # noqa: E402

PROJ = Path(__file__).resolve().parents[2]
WEEKLY = _weekly_dir("parsed")
OUT = PROJ / "data" / "tasks" / "weekly_draft.json"
MAX_ITEMS = 5

# 완료 신호 — 부서방·메일의 서술에서 "했다"를 찾는다. "승인 대기"는 완료가 아니다.
DONE_HINT = re.compile(
    r"완료|보완\s*완|제출(?!\s*예정|\s*마감|마감)|발송|수령|접수|회신|처리했|처리됨|집행했|납부|"
    r"승인(?!\s*대기|\s*요청)|등록했|업로드|전달했|마쳤|끝냈|받았습니다|확인했")



def _weekly_dir(sub: str = "") -> Path:
    """주간보고 데이터 경로 — 설정의 작업공간 아래. 코드 경로와 섞지 않는다."""
    import sys as _s
    _s.path.insert(0, str(Path(__file__).resolve().parents[2] / "core"))
    from lib.config import paths                                 # noqa: PLC0415
    return paths.weekly / sub if sub else paths.weekly


def _core(s: str) -> str:
    s = re.sub(r"^\([^)]*\)[^—]*—\s*", "", s)
    return re.sub(r"[^0-9a-z가-힣]", "", s.lower())


def _sim(a: str, b: str) -> float:
    A = {a[i:i + 2] for i in range(len(a) - 1)}
    B = {b[i:i + 2] for i in range(len(b) - 1)}
    return len(A & B) / min(len(A), len(B)) if A and B else 0.0


def clean(s: str) -> str:
    """보고서 문장으로 다듬는다 — 메일 접두([FW]·RE:)와 호출(@이름)은 소음이다.

    em dash 는 산출물에 쓰지 않는다 (운영자 문체 규칙 2026-08-31) — 콜론으로 바꾼다.
    """
    s = re.sub(r"^(?:\[?(?:re|fw|fwd|답장|전달)\]?\s*[:：]?\s*)+", "", s, flags=re.I)
    s = re.sub(r"^@\S+\s*(?:책임님|리더님|대표님|님)?,?\s*", "", s)
    s = re.sub(r"\s*—\s*", ": ", s)
    return s.strip(" :·-\t")


def briefly(s: str) -> str:
    """개괄식 — 보고서는 서술형 문장이 아니라 명사구다 (운영자 2026-09-01 #0)."""
    s = clean(s)
    s = re.sub(r"\s*(입니다|합니다|했습니다|됩니다|되었습니다|드립니다|하겠습니다|바랍니다|"
               r"해야\s*(합니다|해요|되네요|한다|하고)|주세요|이에요|예요|네요|습니다|했고|하고|"
               r"된다고|다고 합니다|이라고 합니다)[.!~ ]*$", "", s)
    s = re.sub(r"된다고\s*(합니다|함)?\s*$", "됨", s)
    s = re.sub(r"한다고\s*(합니다|함)?\s*$", "함", s)
    s = re.sub(r"[.。!?~]+$", "", s)
    return s[:60].strip()


def _toks(s: str) -> set[str]:
    return {w for w in re.split(r"[^0-9A-Za-z가-힣]+", s) if len(w) >= 2}


def same_issue(a: str, b: str, thr: float = 0.62) -> bool:
    """두 문장이 같은 사안인가 — 칸 안 접기와 칸 사이(전주↔금주↔이슈) 중복 금지가
    같은 잣대를 쓴다. 잣대가 둘이면 한쪽에서 접힌 것이 다른 쪽에서 살아난다(실측)."""
    c, kc = _core(a), _core(b)
    if len(c) < 8 or len(kc) < 8:
        return False

    def share5(x: str, y: str) -> bool:
        s2, l2 = (x, y) if len(x) <= len(y) else (y, x)
        return len(s2) >= 5 and any(s2[i:i + 5] in l2 for i in range(len(s2) - 4))

    if _sim(c, kc) >= thr:
        return True
    # 조사 변형("사용이/사용 가능하여")은 2-gram 만으로 못 잡는다 —
    # 5글자 공통구가 있고 어느 정도 닮았으면 같은 사안으로 접는다
    if len(c) >= 10 and share5(c, kc) and _sim(c, kc) >= 0.42:
        return True
    # 같은 해소를 여러 사람이 다른 말로 알린 경우 ("일반수용비 사례비" 3종 문구 실측)
    if len(c) >= 10 and share5(c, kc) and DONE_HINT.search(a) and DONE_HINT.search(b):
        return True
    tk, ktk = _toks(a), _toks(b)
    return (len(tk) >= 3 and len(ktk) >= 3
            and len(tk & ktk) / min(len(tk), len(ktk)) >= 0.6)


def fold(lines: list[str], thr: float = 0.62) -> list[str]:
    """같은 사안을 칸 안에서 한 번만 — 긴(정보 많은) 문구를 남긴다."""
    kept: list[str] = []
    for ln in sorted(lines, key=lambda x: (-len(x), x)):
        if not any(same_issue(ln, k, thr) for k in kept):
            kept.append(ln)
    order = {ln: i for i, ln in enumerate(lines)}
    return sorted(kept, key=lambda x: order.get(x, 99))


def split_cell(cell: str) -> list[str]:
    """보고서 칸 → 항목. 줄과 「 / 」로 나누고, 날짜만 있는 조각은 다음 항목에 붙인다."""
    parts, pend = [], ""
    for ln in re.split(r"[\r\n\x0b]+", cell or ""):
        ln = ln.strip(" -·•\t")
        if not ln or ln in ("-", "—"):
            continue
        for seg in re.split(r"\s+/\s+", ln):
            seg = seg.strip(" /")
            if not seg:
                continue
            if len(seg) <= 12 and re.fullmatch(r"\d{1,2}월\s*\d{1,2}일.*|\d{1,2}/\d{1,2}\S*", seg):
                pend = seg
                continue
            parts.append(f"{pend} {seg}".strip() if pend else seg)
            pend = ""
    return parts


def next_tuesday(today: dt.date) -> dt.date:
    return today + dt.timedelta(days=(1 - today.weekday()) % 7)


def latest_weekly(before: str = "9999") -> dict | None:
    """직전 **제출본**. 이번 주기에 미리 올라온 보고서(오늘자 등)는 비교 기준이 아니다."""
    best = None
    for f in WEEKLY.glob("*.json"):
        try:
            d = json.loads(f.read_text(encoding="utf-8"))
        except (ValueError, OSError):
            continue
        if isinstance(d, dict) and d.get("org") == "연구기획사업부" and d.get("items") \
                and (d.get("week") or "") <= before:
            if best is None or d.get("week", "") > best.get("week", ""):
                best = d
    return best


def col_of(head, pat):
    for h in head:
        if re.search(pat, h):
            return h
    return None


def week_evidence(index, tasks, wf: str, wt: str) -> dict[str, list[str]]:
    """전주 창(wf~wt) 안의 완료 증거를 과제별로 모은다: 원장 + 부서방 원문 + 메일."""
    ev: dict[str, list[str]] = defaultdict(list)

    drive_n: dict[str, int] = defaultdict(int)
    for t in tasks:                                   # 1) 원장 완료 기록
        pid = t.get("project_id")
        stream = (t.get("source") or {}).get("stream")
        if stream == "messenger":
            continue          # 부서방은 아래에서 원문을 조각 단위로 읽는다 — 140자 합성 제목은 오염원
        if pid and t["state"] == "done" and wf <= str(t.get("done_at") or "")[:10] <= wt:
            if stream == "drive":
                # 파일 이벤트를 낱줄로 나열하면 실적 칸이 파일 목록이 된다 — 과제당 한 줄로 합산
                m = re.search(r"산출물\s*(\d+)건", t["title"])
                drive_n[pid] += int(m.group(1)) if m else 1
                continue
            title = re.sub(r"^\([^)]*\)[^—]*—\s*", "", t["title"].strip())
            ev[pid].append(clean(title)[:80])


    if EO.MSG.is_dir():                               # 2) 부서방 원문 — 조각 단위
        for f in sorted(EO.MSG.glob("*/*.jsonl")):
            for line in f.read_text(encoding="utf-8").splitlines():
                if not line.strip():
                    continue
                m = json.loads(line)
                day = (m.get("kst") or "")[:10]
                text = m.get("text") or ""
                if not (wf <= day <= wt) or text.startswith("🔔"):
                    continue
                cur = EO.match(text[:80], index)      # 머리의 [과제] 표기
                for seg in re.split(r"[\n\x0b]+|(?=\[[^\]]{2,20}\])", text):
                    seg = re.sub(r"\s+", " ", seg).strip(" -·•")
                    if len(seg) < 10:
                        continue
                    cur = EO.match(seg, index) or cur
                    if cur and DONE_HINT.search(seg):
                        if len(seg) > 50 and "," in seg[15:]:
                            seg = seg[:15 + seg[15:].index(",")]
                        ev[cur].append(briefly(seg)[:80])

    if EO.MAIL.is_dir():                              # 3) 메일 — 회신·완료성 제목
        for f in EO.MAIL.glob("*/*/[0-9]*.json"):
            try:
                mo = (json.loads(f.read_text())).get("mail") or {}
            except (ValueError, OSError):
                continue
            day = (mo.get("receivedTime") or "")[:10]
            subj = re.sub(r"\s+", " ", (mo.get("subject") or "")).strip()
            if not (wf <= day <= wt):
                continue
            is_reply = bool(re.match(r"(?:re|답장)\s*[:：]", subj, re.I))
            subj = re.sub(r"^(?:(?:re|fw|fwd|답장|전달)\s*[:：]\s*)+", "", subj, flags=re.I)
            pid = EO.match(subj, index)
            if pid and (is_reply or DONE_HINT.search(subj)):
                ev[pid].append(subj[:80] + (" (회신 수령)" if is_reply else ""))
    return {k: fold(v) for k, v in ev.items()}, dict(drive_n)


def ssot_changes(index, wf: str, wt: str):
    """SSOT 부서 트리의 창 안 파일 변경 — 과제별 (파일들, 건수) + 부서 총계.

    부서는 SSOT 로 산출물을 만든다 (운영자 2026-09-01 #11) — 여기 변동이 곧 한 주의 일이다.
    """
    base = EO.SSOT / "{{DEPT_ROOT}}"
    per: dict[str, list[str]] = defaultdict(list)
    total = 0
    if not base.is_dir():
        return per, 0
    for f in base.rglob("*"):
        try:
            if (f.is_dir() or f.name in EO.SKIP_NAMES or f.name.startswith(".")
                    or any(d in f.parts for d in EO.SKIP_DIRS)):
                continue
            mday = dt.date.fromtimestamp(f.stat().st_mtime).isoformat()
        except OSError:
            continue
        if wf <= mday <= wt:
            total += 1
            pid = EO.match(EO.nfc(str(f.parent.relative_to(base))), index)
            per[pid or ""].append(EO.nfc(f.name))
    return per, total


def load_profiles() -> dict:
    f = PROJ / "data" / "knowledge" / "project_profiles.json"
    if not f.exists():
        return {}
    return {k: v for k, v in json.loads(f.read_text(encoding="utf-8")).items()
            if not k.startswith("_")}


def profile_for(pid, pjt: str, profiles: dict):
    if pid and pid in profiles:
        return profiles[pid]
    for k, v in profiles.items():
        if k.startswith("row:") and k[4:].lower() in pjt.lower():
            return v
    return {}


def main() -> int:
    wk = None   # 회의일 확정 후 컷오프로 다시 고른다
    if False:
        print("주간보고 파싱본 없음 — 초안 생략")
        return 0
    ledger_f = PROJ / "data" / "tasks" / "ledger.jsonl"
    tasks = [json.loads(x) for x in ledger_f.read_text(encoding="utf-8").splitlines()
             if x.strip()] if ledger_f.exists() else []

    today = dt.date.today()
    rpt = next_tuesday(today)
    mfile = PROJ / "data" / "knowledge" / "weekly_meeting.json"
    if mfile.exists():        # 회의가 밀린 주는 사람이 알려준다 (기본: 화요일)
        try:
            ov = dt.date.fromisoformat(json.loads(mfile.read_text())["meeting"])
            if ov >= today:
                rpt = ov
        except (ValueError, KeyError):
            pass
    # 전주 창 = 직전 보고일 ~ 회의 전날 (운영자 2026-09-01: "8/25~8/31 변동사항 모두")
    plan_to = (rpt + dt.timedelta(days=6)).isoformat()
    wk = latest_weekly(before=(rpt - dt.timedelta(days=4)).isoformat())
    if not wk:
        print("직전 제출본 없음 — 초안 생략")
        return 0

    head = wk.get("header", [])
    c_no, c_kind = col_of(head, r"^NO|번호"), col_of(head, r"구분")
    c_pjt, c_plan = col_of(head, r"PJT|과제"), col_of(head, r"금주|계획")
    c_issue = col_of(head, r"이슈")

    wf = wk.get("week") or (rpt - dt.timedelta(days=7)).isoformat()
    wt = (rpt - dt.timedelta(days=1)).isoformat()

    index = E.project_index()
    evidence, drive_n = week_evidence(index, tasks, wf, wt)
    ssot_per, ssot_total = ssot_changes(index, wf, wt)
    by_pid: dict[str, list[dict]] = {}
    for t in tasks:
        if t.get("project_id"):
            by_pid.setdefault(t["project_id"], []).append(t)

    ax_file = PROJ / "data" / "knowledge" / "ax_summary.json"
    ax = json.loads(ax_file.read_text(encoding="utf-8")) if ax_file.exists() else {}
    profiles = load_profiles()

    rows = []
    for it in wk["items"]:
        pjt = str(it.get(c_pjt) or "").strip()
        pid = EO.match(pjt, index) if pjt else None

        # 연구기획사업부 AX 행은 우리 자신의 일 — AI가 유지하는 주간 요약을 쓴다 (R11)
        if "연구기획" in pjt and "AX" in pjt and ax.get("전주"):
            ax_prev = list(ax["전주"])[:MAX_ITEMS - 1]
            ax_prev.append(f"SSOT 산출물 변경 {ssot_total}건 ({wf[5:].replace('-', '/')}"
                           f"~{wt[5:].replace('-', '/')}, 전 과제 합산)")
            rows.append({"no": str(it.get(c_no) or "").strip(),
                         "구분": str(it.get(c_kind) or "").strip(),
                         "pjt": pjt, "pid": None,
                         "전주": "\n".join("· " + x for x in ax_prev),
                         "전주_출처": "증거",
                         "금주": "\n".join("· " + x for x in ax.get("금주", [])[:MAX_ITEMS]),
                         "이슈": ""})
            continue

        ev = evidence.get(pid or "", [])
        mine = by_pid.get(pid or "", [])
        prev_lines = [clean(x) for x in split_cell(str(it.get(c_plan) or ""))]

        # --- 전주 실적: 지난주 계획 중 증거가 있는 것 + 계획 밖 추가 수행
        done_lines, carry_lines = [], []
        used = set()
        for ln in prev_lines:
            c = _core(ln)
            hit = next((e for e in ev if same_issue(ln, e, 0.5)), None)
            if hit:
                done_lines.append(ln)
                used.add(hit)
            else:
                carry_lines.append(ln)
        extra = [e for e in ev if e not in used
                 and not any(same_issue(e, dl, 0.5) for dl in done_lines)]
        prof = profile_for(pid, pjt, profiles)
        if prof.get("전주완료"):      # 사람이 "지난주 전량 완료"라고 확언한 과제
            done_lines += carry_lines
            carry_lines = []
        # 프로필의 단계(사람이 준 현황)가 첫 줄 — 기계 증거는 뒤에 (운영자 브리핑이 정본)
        head_lines = ([prof["단계"]] if prof.get("단계") else []) + \
            [briefly(x) for x in prof.get("전주", [])]
        n_files = (len(ssot_per.get(pid or "", [])) if pid else 0) + drive_n.get(pid or "", 0)
        files_line = [f"산출물·SSOT 갱신 {n_files}건"] if n_files else []
        if prof.get("전주완료"):
            # 사람이 "전량 완료"라고 확언 — 낱개 나열은 중복이다. 단계 + 정량만.
            auto_lines = files_line
        else:
            auto_lines = [briefly(x) for x in fold(done_lines + extra)][:2] + files_line
        prev_cell_lines = fold(head_lines + auto_lines)
        prev_src = "증거" if prev_cell_lines else "없음"

        # --- 이슈 (금주보다 먼저 — 금주가 이슈와 겹치면 이슈 쪽만 남긴다)
        issue_lines = fold([briefly(re.sub(r"^\([^)]*\)[^—]*—\s*", "", t["title"]))
                            for t in mine
                            if t["state"] == "blocked" or t.get("needs_check")])

        # --- 금주 계획: 기한이 이번 창인 것 > 못 끝낸 지난주 계획(지속) > 지난주 새 요청
        due_items = [t for t in mine if t["state"] in ("planned", "doing")
                     and rpt.isoformat() <= str(t.get("due") or "")[:10] <= plan_to]
        # 채팅·메일 원문은 보고서 계획 칸에 싣지 않는다 (운영자 2026-09-01: 개괄식, 계획 기반).
        # 그런 항목은 대시보드 §01이 담는다. 공식 문서 유래(주간보고·회의록·정정)만 계획이 된다.
        fresh = [t for t in mine if t["state"] in ("planned", "doing")
                 and not t.get("due") and not t.get("needs_check")
                 and (t.get("source") or {}).get("stream")
                 in ("weekly_report", "action_ledger", "correction")
                 and wf <= str(t.get("reported_at") or t.get("opened_at") or "")[:10] <= wt]
        # 지난 날짜만 박힌 계획은 이월하지 않는다 — 이미 지난 일은 실적이거나 소멸이다
        def stale(ln: str) -> bool:
            ds = [(int(m.group(1)), int(m.group(2))) for m in
                  re.finditer(r"(\d{1,2})\s*[/월]\s*(\d{1,2})일?", ln)]
            if not ds:
                return False
            return all(dt.date(rpt.year, mo, da) < rpt for mo, da in ds
                       if 1 <= mo <= 12 and 1 <= da <= 31)
        carry_lines = [ln for ln in carry_lines if not stale(ln)]

        raw_plan = ([clean(re.sub(r"^\([^)]*\)[^—]*—\s*", "", t["title"]))[:70]
                     + (f" (기한 {t['due'][5:10].replace('-', '/')})" if t.get("due") else "")
                     for t in due_items]
                    + [ln + " (지속)" for ln in carry_lines]
                    + [clean(re.sub(r"^\([^)]*\)[^—]*—\s*", "", t["title"]))[:70]
                       for t in fresh])

        # 이미 전주에 실적으로 오른 것, 이슈로 오른 것을 금주에 또 쓰지 않는다 (교차 중복 금지)
        others = prev_cell_lines + issue_lines
        manual_plan = [briefly(x) for x in prof.get("금주", [])]
        auto_plan = [briefly(ln) for ln in raw_plan
                     if not any(same_issue(ln, o, 0.55) for o in others)
                     and not any(same_issue(ln, m2, 0.5) for m2 in manual_plan)]
        plan_lines = fold(manual_plan + auto_plan[:max(0, MAX_ITEMS - len(manual_plan))])

        def cell(lines, empty):
            if not lines:
                return empty
            shown = lines[:MAX_ITEMS]
            more = len(lines) - MAX_ITEMS
            return "\n".join("· " + x for x in shown) + (f"\n· 외 {more}건" if more > 0 else "")

        rows.append({
            "no": str(it.get(c_no) or "").strip(),
            "구분": str(it.get(c_kind) or "").strip(),
            "pjt": pjt, "pid": pid,
            "전주": cell(prev_cell_lines, "(실적 확인 필요)"), "전주_출처": prev_src,
            "금주": cell(plan_lines, "(작성 필요)"),
            "이슈": cell(issue_lines, ""),
        })

    OUT.write_text(json.dumps({
        "report_date": rpt.isoformat(), "week_from": wf, "week_to": wt,
        "plan_to": plan_to, "base_week": wk.get("week"),
        "base_file": wk.get("source_file"), "generated": today.isoformat(),
        "rows": rows,
    }, ensure_ascii=False, indent=1), encoding="utf-8")
    auto = sum(1 for r in rows if r["전주_출처"] == "증거")
    print(f"· 주간보고 초안 — {rpt} 보고분 · 행 {len(rows)} · 실적 증거 {auto}행 "
          f"(전주 {wf}~{wt} · 금주 {rpt}~{plan_to})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
