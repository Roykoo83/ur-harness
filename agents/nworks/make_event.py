#!/usr/bin/env python3
"""네이버웍스 기본 캘린더에 일정을 만든다.

쓰기 스코프 `calendar` 가 필요하다. `calendar.read` 만으로는 invalid_scope 로 막힌다.
Developer Console 에서 scope 를 켠 뒤 **같은 작업으로 UA 재동의(R1)** 를 돌려야
메일 수집이 멈추지 않는다 (HANDOVER 실측 2026-08-25).

사용:
    make_event.py --user {{OPERATOR_ACCOUNT}} --date 2026-09-30 \
        --summary "제목" [--location "장소"] [--desc-file 설명.txt] [--dry-run]
"""
import argparse
import json
import sys
from pathlib import Path

PROJ = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJ / "scripts"))
from nworks.client import NworksClient  # noqa: E402


def next_day(d: str) -> str:
    import datetime
    return (datetime.date.fromisoformat(d) + datetime.timedelta(days=1)).isoformat()


def build(args) -> dict:
    """종일 일정은 start/end 를 date 로 주고, end 는 다음 날(exclusive)."""
    ev = {
        "eventComponents": [{
            "summary": args.summary,
            "start": {"date": args.date},
            "end": {"date": next_day(args.date)},
            "transparency": "OPAQUE",
        }]
    }
    c = ev["eventComponents"][0]
    if args.location:
        c["location"] = args.location
    if args.desc_file:
        c["description"] = Path(args.desc_file).read_text(encoding="utf-8")
    if args.reminder_days is not None:
        # 종일 일정은 분 단위 offset. 하루 전 09:00 = 전날 09시 → -900분 기준이 아니라
        # API 는 start 기준 음수 분을 받는다. 하루 전 = -1440 + 540(09:00) = -900
        c["reminders"] = [{"method": "DISPLAY",
                           "trigger": -(args.reminder_days * 1440) + 540}]
    return ev


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--user", required=True, help="이메일 또는 userId")
    ap.add_argument("--date", required=True, help="YYYY-MM-DD (종일)")
    ap.add_argument("--summary", required=True)
    ap.add_argument("--location", default="")
    ap.add_argument("--desc-file", default="")
    ap.add_argument("--reminder-days", type=int, default=1)
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()

    body = build(a)
    if a.dry_run:
        print(f"POST /users/{a.user}/calendar/events")
        print(json.dumps(body, ensure_ascii=False, indent=2))
        return 0

    r = NworksClient().call("calendar.create", {"user_id": a.user}, json_body=body)
    print(json.dumps(r, ensure_ascii=False, indent=2)[:800])
    return 0


if __name__ == "__main__":
    sys.exit(main())
