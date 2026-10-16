"""A3 calendar collector — 타깃 구성원(연구기획사업부+대표)의 캘린더를 SA 토큰으로 수집.

대상: scripts/calendar_targets.json (전사 확산 시 Roy가 리스트 교체)
창: 전월·당월·익월 3개 월 (calendar.events는 커서 없음, 31일 창 제약 → 월 단위 조회)

저장 예외 규약: 캘린더는 원천이 가변(수정·취소)이므로 00_raw/calendar/는
불변 로그가 아니라 **최신 상태 미러**다 — 실행마다 월 파일을 덮어쓴다 (README 명시).
  output/raw_calendar/<user-local>/<YYYY-MM>.json   (해당 월 events 원문)
  output/stage/calendar/<YYYY-MM>.md                (타깃 통합·eventId 중복 제거 일정표)
  output/stage/calendar/index.md

Read-only: GET only (calendar-default-event-user-list).
"""
import json
import sys
from datetime import date
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from nworks.auth import get_token  # noqa: E402
from nworks.client import NworksClient  # noqa: E402
from nworks.endpoints import API_HOST, V  # noqa: E402


def _month_bounds(y: int, m: int) -> tuple[str, str]:
    last = (date(y + (m == 12), (m % 12) + 1, 1) - date(y, m, 1)).days
    return (f"{y:04d}-{m:02d}-01T00:00:00+09:00",
            f"{y:04d}-{m:02d}-{last:02d}T23:59:59+09:00")


def _months(today: date) -> list[tuple[int, int]]:
    y, m = today.year, today.month
    prev = (y - (m == 1), m - 1 or 12)
    nxt = (y + (m == 12), (m % 12) + 1)
    return [prev, (y, m), nxt]


def _resolve_user_ids(client: NworksClient, emails: list[str]) -> dict:
    by_email = {}
    for u in client.paginate("directory.users", item_field="users", params={"count": 100}):
        if (u.get("email") or "").lower() in emails:
            by_email[u["email"].lower()] = u["userId"]
    return by_email


def _fetch_month_events(user_id: str, frm: str, until: str) -> list:
    tok = get_token(["calendar.read"])
    r = requests.get(API_HOST + V + f"/users/{user_id}/calendar/events",
                     headers={"Authorization": f"Bearer {tok}"},
                     params={"fromDateTime": frm, "untilDateTime": until}, timeout=30)
    if r.status_code != 400 and r.status_code >= 400:
        raise RuntimeError(f"calendar events {r.status_code}: {r.text[:200]}")
    return (r.json().get("events") or []) if r.status_code == 200 else []


def collect(project_root: Path, today: date) -> dict:
    cfg = json.loads((project_root / "scripts" / "calendar_targets.json").read_text())
    targets = cfg["targets"]
    emails = [t["email"].lower() for t in targets]
    name_by_email = {t["email"].lower(): t["name"] for t in targets}

    client = NworksClient()
    ids = _resolve_user_ids(client, emails)
    missing = [e for e in emails if e not in ids]

    raw_root = project_root / "output" / "raw_calendar"
    months = _months(today)
    merged: dict = {}  # (ym, event_key) -> {info, attendee names}
    fetched = 0
    for email in emails:
        if email not in ids:
            continue
        local = email.split("@")[0]
        for (y, m) in months:
            frm, until = _month_bounds(y, m)
            events = _fetch_month_events(ids[email], frm, until)
            fetched += 1
            udir = raw_root / local
            udir.mkdir(parents=True, exist_ok=True)
            (udir / f"{y:04d}-{m:02d}.json").write_text(
                json.dumps({"user": email, "month": f"{y:04d}-{m:02d}",
                            "events": events}, ensure_ascii=False, indent=1))
            for e in events:
                for comp in e.get("eventComponents") or []:
                    start = comp.get("start", {})
                    when = start.get("dateTime") or start.get("date") or ""
                    ym = when[:7]
                    # eventId는 참석자별 사본마다 다르므로 시각+제목으로 병합
                    key = (ym, when, (comp.get("summary") or "").strip().lower())
                    ent = merged.setdefault(key, {
                        "when": when, "summary": comp.get("summary") or "(제목 없음)",
                        "attendee_count": len(comp.get("attendees") or []),
                        "ours": set()})
                    ent["ours"].add(name_by_email[email])

    stage_root = project_root / "output" / "stage" / "calendar"
    if stage_root.exists():
        for old in stage_root.glob("*.md"):
            old.unlink()
    stage_root.mkdir(parents=True, exist_ok=True)

    # 창(3개 월) 밖 시작일 = 반복 일정 마스터/예외 인스턴스 → 별도 파일로 분리
    window_yms = {f"{y:04d}-{m:02d}" for (y, m) in months}
    by_month: dict = {}
    recurring: list = []
    for (ym, *_), ent in merged.items():
        if ym in window_yms:
            by_month.setdefault(ym, []).append(ent)
        else:
            recurring.append(ent)
    if recurring:
        recurring.sort(key=lambda x: x["when"])
        lines = ["# 반복·정기 일정 (마스터/예외 — RRULE 미전개, 원 시작일 기준)", ""]
        for ent in recurring:
            ours = "·".join(sorted(ent["ours"]))
            lines.append(f"- {ent['when'][:16]}~ {ent['summary']} — {ours}")
        (stage_root / "recurring.md").write_text("\n".join(lines) + "\n")
    for ym, ents in sorted(by_month.items()):
        ents.sort(key=lambda x: x["when"])
        lines = [f"# {ym} 일정 (연구기획사업부+대표 · stage 자동 재생성)", ""]
        cur_day = None
        for ent in ents:
            day = ent["when"][:10]
            if day != cur_day:
                lines.append(f"\n## {day}")
                cur_day = day
            t = ent["when"][11:16] or "종일"
            ours = "·".join(sorted(ent["ours"]))
            n = ent["attendee_count"]
            lines.append(f"- **{t}** {ent['summary']} — {ours}" + (f" (참석 {n}명)" if n else ""))
        (stage_root / f"{ym}.md").write_text("\n".join(lines) + "\n")

    idx = ["# 캘린더 stage 색인", "",
           f"대상: {', '.join(t['name'] for t in targets)} (calendar_targets.json)", ""]
    for ym in sorted(by_month):
        idx.append(f"- [{ym}]({ym}.md) — {len(by_month[ym])}건")
    if recurring:
        idx.append(f"- [반복·정기 일정](recurring.md) — {len(recurring)}건 (RRULE 미전개)")
    (stage_root / "index.md").write_text("\n".join(idx) + "\n")

    return {"targets": len(emails), "unresolved": missing, "month_queries": fetched,
            "events_unique": len(merged), "recurring_masters": len(recurring),
            "months": {ym: len(v) for ym, v in sorted(by_month.items())}}


if __name__ == "__main__":
    root = Path(__file__).resolve().parents[2]
    print(json.dumps(collect(root, date.today()), ensure_ascii=False))
