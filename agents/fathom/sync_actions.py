#!/usr/bin/env python3
"""확정된 회의록에서 액션 아이템을 뽑아 누적 원장(actions.json)을 유지한다.

이것이 **롱레인지 연결**의 실체다. 매주 회의록이 독립 문서로 흩어지면
"지난주에 뭘 하기로 했더라"가 사라진다. 액션을 한 원장에 모아 두면
다음 회의록 초안에 미완료 항목이 자동으로 실린다.

    python3 sync_actions.py            # 회의록 → 원장 갱신
    python3 sync_actions.py --report   # 미완료 액션 현황 출력

액션 상태는 회의록의 체크박스로 표현한다: ☐ 미완료 / ☑ 완료 / ⊘ 취소
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MIN, ACTIONS = ROOT / "output" / "minutes", ROOT / "data" / "actions.json"
ROUTES = ROOT / "data" / "routes.json"


def minutes_dirs() -> list[Path]:
    """기본 출력 폴더 + routes.json 에 등록된 프로젝트 폴더.

    과제 회의록은 해당 프로젝트 볼트에 두는 편이 찾기 쉽다. 그러면 액션이
    원장에서 빠지므로, 경로표를 두고 여기서도 읽는다.
    """
    dirs = [MIN]
    if ROUTES.exists():
        for r in json.loads(ROUTES.read_text(encoding="utf-8")).get("routes", []):
            d = Path(r["minutes_dir"]).expanduser()
            if d.is_dir():
                dirs.append(d)
    return dirs

FM_RE = re.compile(r"^---\n(.*?)\n---", re.S)
# 「향후 일정」 절의 표만 읽는다. 이월 표는 원본 회의에서 이미 잡혔으므로 제외된다.
# 절 번호와 이모지 표기가 문서마다 다르다: "## 5. 향후 일정", "## 📋 향후 일정 (액션 아이템)".
SEC_RE = re.compile(r"^##[^\n]*향후\s*일정[^\n]*\n(.*?)(?=^##\s|\Z)", re.S | re.M)
ROW_RE = re.compile(r"^\|\s*(?P<no>[^|]*?)\s*\|\s*(?P<action>[^|]+?)\s*\|"
                    r"\s*(?P<owner>[^|]*?)\s*\|\s*(?P<due>[^|]*?)\s*\|"
                    r"\s*(?P<state>[^|]*?)\s*\|\s*$", re.M)
STATE = {"☑": "done", "☒": "done", "x": "done", "X": "done",
         "⊘": "cancelled", "취소": "cancelled"}


def front_matter(text: str) -> dict:
    m = FM_RE.match(text)
    if not m:
        return {}
    out = {}
    for line in m.group(1).splitlines():
        if ":" in line:
            k, v = line.split(":", 1)
            out[k.strip()] = v.strip()
    return out


def clean(s: str) -> str:
    """마크다운 강조·주석을 걷어낸다."""
    s = re.sub(r"\*\*|\*|`", "", s or "")
    s = re.sub(r"<[^>]+>", "", s)
    return s.strip()


def action_key(meeting_id: str, action: str) -> str:
    h = hashlib.sha1(f"{meeting_id}|{action}".encode()).hexdigest()[:10]
    return f"A-{h}"


def collect() -> list[dict]:
    out = []
    for f in sorted(f for d in minutes_dirs() for f in d.glob("*.md")):
        if f.name.endswith(".draft.md"):
            continue                      # 초안은 원장에 넣지 않는다
        text = f.read_text(encoding="utf-8")
        fm = front_matter(text)
        if fm.get("status") == "draft":
            continue
        sec = SEC_RE.search(text)
        if not sec:
            continue
        for m in ROW_RE.finditer(sec.group(1)):
            act = clean(m.group("action"))
            if not act or act in ("액션", "---") or set(act) <= {"-"}:
                continue
            state_raw = clean(m.group("state"))
            out.append({
                "id": action_key(fm.get("meeting_id", f.stem), act),
                "action": act,
                "owner": clean(m.group("owner")) or None,
                "due": clean(m.group("due")) or None,
                "status": STATE.get(state_raw, "open"),
                "meeting_id": fm.get("meeting_id", f.stem),
                "meeting_type": fm.get("type", ""),
                "meeting_date": fm.get("date", f.stem[:10]),
                "source": f.name,
            })
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--report", action="store_true")
    args = ap.parse_args()

    found = collect()
    prev = {a["id"]: a for a in
            (json.loads(ACTIONS.read_text(encoding="utf-8")) if ACTIONS.exists() else [])}

    merged = {}
    for a in found:
        old = prev.get(a["id"], {})
        # 한 번 done 이 된 액션은 회의록 편집 실수로 되돌아가지 않게 유지한다
        if old.get("status") == "done" and a["status"] == "open":
            a["status"] = "done"
        a["first_seen"] = old.get("first_seen") or a["meeting_date"]
        merged[a["id"]] = a
    for k, v in prev.items():                 # 회의록에서 사라진 액션도 보존
        merged.setdefault(k, v)

    rows = sorted(merged.values(), key=lambda a: (a["meeting_date"], a["action"]))
    ACTIONS.parent.mkdir(parents=True, exist_ok=True)
    ACTIONS.write_text(json.dumps(rows, ensure_ascii=False, indent=1), encoding="utf-8")

    op = [a for a in rows if a["status"] == "open"]
    print(f"액션 {len(rows)}건 · 미완료 {len(op)}건 → {ACTIONS}")

    if args.report and op:
        today = dt.date.today().isoformat()
        print("\n미완료 액션")
        for a in op:
            late = ""
            due = (a.get("due") or "").replace("~", "").strip()
            if re.match(r"\d{4}-\d{2}-\d{2}$", due) and due < today:
                late = "  ⚠️기한초과"
            print(f"  [{a['meeting_date']}] {a['action'][:56]:58} "
                  f"{a.get('owner') or '-':10} {a.get('due') or '-'}{late}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
