#!/usr/bin/env python3
"""후보를 태스크 원장에 병합하고 주간 정산을 적용한다.

    python3 scripts/tasks/ledger.py

산출물
    data/tasks/ledger.jsonl          현재 살아 있는 태스크 (화면에 뜨는 것)
    data/tasks/archive/YYYY-WW.jsonl 보고를 마치고 내려간 태스크
    data/tasks/events.jsonl          상태 변화 기록 (append-only, 지우지 않는다)

**멱등해야 한다.** 같은 입력으로 두 번 돌려도 태스크가 늘거나 이벤트가 겹치면 안 된다.
그러지 않으면 시간당 실행에서 보드가 매 시간 부풀어 오른다.
"""
from __future__ import annotations

import datetime as dt
import json
import re
from pathlib import Path

PROJ = Path(__file__).resolve().parents[2]
D = PROJ / "data" / "tasks"
LEDGER, EVENTS, ARCHIVE = D / "ledger.jsonl", D / "events.jsonl", D / "archive"
SEED, CANDS = D / "seed.jsonl", D / "candidates.jsonl"
OBSERVED = D / "candidates_observed.jsonl"

# 기한이 지나고 이만큼 지나도록 새 증거가 없으면 **완료로 치지 않고 물어본다.**
# 침묵은 완료가 아니라 모름이다.
STALE_DAYS = 3

# 정산은 **주간회의가 끝난 뒤**에 한다. 화요일 00시를 경계로 잡으면 그날 새벽 사이클이
# 지난주 완료를 전부 내려버려, 정작 회의 화면에 「지난주 실적 0건」이 뜬다.
# 2026-09-08 01:13 사이클에서 실제로 28건이 회의 전에 내려갔다.
SETTLE_HOUR = 18

# 보드에 올리지 않을 것들. **주간 의사결정 화면은 무엇을 정할지 보는 곳**이라
# 보안 사고·개인정보 노출 같은 항목은 여기 자리가 아니다(Roy 지시 2026-08-25).
# 내용 자체를 지우지는 않는다 — 원장에서 빼서 화면에 뜨지 않게만 한다.
EXCLUDE = re.compile(
    r"급여명세|이체확인증|통장\s*사본|계좌\s*사본|민감\s*자료|보안\s*폴더|"
    r"비밀번호|패스워드|로그인\s*정보")

# 상태의 세기. 약한 증거가 강한 상태를 되돌리지 못하게 한다 —
# 완료된 일이 다음 수집에서 '계획'으로 되살아나면 보드를 믿을 수 없다.
RANK = {"planned": 0, "blocked": 1, "doing": 2, "done": 3}


def read(p: Path) -> list[dict]:
    if not p.exists():
        return []
    return [json.loads(x) for x in p.read_text(encoding="utf-8").splitlines() if x.strip()]


def write(p: Path, rows: list[dict]) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows),
                 encoding="utf-8")


WHEN_DATE = re.compile(r"20\d{2}-\d{2}-\d{2}")


def when_date(v) -> str | None:
    """증거의 when 에서 날짜만. `WM-2026-08-11` 같은 회의 ID 에도 날짜가 들어 있다."""
    m = WHEN_DATE.search(str(v or ""))
    return m.group(0) if m else None


def can_settle(today: dt.date, now: dt.datetime) -> bool:
    """지금 정산해도 되는가. **주간회의가 끝난 뒤**여야 한다.

    화요일 00시를 경계로 잡으면 그날 새벽 사이클이 지난주 완료를 전부 내려버려,
    정작 회의 화면에 「지난주 실적 0건」이 뜬다. 완료가 화요일 보고까지 보드에
    남아 있어야 한다는 설계(§2)를 구현이 어기고 있었다.
    """
    return today != tuesday_of(today) or now.hour >= SETTLE_HOUR


def tuesday_of(d: dt.date) -> dt.date:
    """그 주의 화요일. 주간회의가 화요일이라 정산 경계도 화요일이다."""
    return d - dt.timedelta(days=(d.weekday() - 1) % 7)


def merge_one(cur: dict, new: dict) -> tuple[dict, list[str]]:
    """기존 태스크에 새 증거를 반영. (병합 결과, 변화 설명)"""
    changed = []
    out = dict(cur)
    out.setdefault("evidence", [])

    ref = new.get("source", {})
    if not any(e.get("stream") == ref.get("stream") and e.get("ref") == ref.get("ref")
               for e in out["evidence"]):
        out["evidence"].append(ref)

    if RANK.get(new["state"], 0) > RANK.get(out["state"], 0):
        changed.append(f'{out["state"]}→{new["state"]}')
        out["state"] = new["state"]
        if new["state"] == "done":
            out["done_at"] = new.get("done_at") or dt.date.today().isoformat()

    # 비어 있던 칸만 채운다. **이미 아는 값을 덮어쓰지 않는다** — 나중에 온 자료가
    # 더 정확하다는 보장이 없고, 사람이 정정한 값을 기계가 되돌리면 신뢰가 무너진다.
    for k in ("owner", "due", "project_id", "project_name", "issue"):
        if out.get(k) in (None, "") and new.get(k) not in (None, ""):
            out[k] = new[k]
            changed.append(f"{k}={new[k]}")
    return out, changed


def main() -> int:
    today = dt.date.today()
    this_tue = tuesday_of(today)
    ledger = {t["task_id"]: t for t in read(LEDGER)}
    events = read(EVENTS)
    seen_events = {(e["at"], e["task_id"], e["what"]) for e in events}

    # 시드는 원장이 비어 있을 때 한 번만 넣는다. 매번 넣으면 정산으로 내려간 태스크가
    # 다음 실행에서 되살아난다.
    # 원장 계열을 먼저 넣는다. 같은 태스크를 관측이 먼저 만들면 신뢰도가 낮게 굳는다.
    incoming = read(CANDS) + read(OBSERVED)
    if not ledger:
        incoming = read(SEED) + incoming
        print(f"· 원장이 비어 있어 시드 {len(read(SEED))}건을 함께 넣는다")

    # 이미 정산돼 내려간 태스크는 다시 올리지 않는다. 스트림(주간보고 등)에는 그 항목이
    # 계속 남아 있어서, 막지 않으면 **매 실행마다 들어왔다 나가기를 반복**한다 —
    # 시간당으로 돌리면 이벤트 로그가 하루 24번씩 부푼다.
    archived: dict[str, str] = {}
    for f in sorted(ARCHIVE.glob("*.jsonl")) if ARCHIVE.is_dir() else []:
        for t in read(f):
            archived[t["task_id"]] = t.get("reported_at") or ""

    added = updated = revived = skipped = dropped = 0
    for new in incoming:
        if EXCLUDE.search(new.get("title", "")):
            dropped += 1
            continue
        tid = new["task_id"]
        if tid in archived and tid not in ledger:
            # 같은 일이 **나중 주차**에 다시 올라오면 되살린다. 날짜끼리 비교하면
            # 08/12 완료 > 08/11 정산주차가 되어 매번 되살아난다 — 주차로 비교해야 한다.
            raw = str(new.get("done_at") or new.get("due")
                      or (new.get("source") or {}).get("when") or "")[:10]
            try:
                wk = tuesday_of(dt.date.fromisoformat(raw)).isoformat()
            except ValueError:
                wk = ""
            if not (wk and wk > archived[tid]):
                skipped += 1
                continue
            revived += 1
        if tid not in ledger:
            row = dict(new)
            row["evidence"] = [new.get("source", {})]
            row["opened_at"] = row.get("opened_at") or today.isoformat()
            ledger[tid] = row
            added += 1
            ev = {"at": today.isoformat(), "task_id": tid, "what": "생성",
                  "state": row["state"], "title": row["title"][:80]}
        else:
            merged, changed = merge_one(ledger[tid], new)
            ledger[tid] = merged
            if not changed:
                continue
            updated += 1
            ev = {"at": today.isoformat(), "task_id": tid, "what": " · ".join(changed),
                  "state": merged["state"], "title": merged["title"][:80]}
        key = (ev["at"], ev["task_id"], ev["what"])
        if key not in seen_events:
            events.append(ev)
            seen_events.add(key)

    # ---- 완료일 보정 — `done_at` 이 비면 정산 관문을 영영 통과하지 못한다.
    # 회의록 액션과 8/18 시드가 그렇게 3주 넘게 남아 「완료 N건」을 부풀렸다.
    #
    # 값은 **우리가 아는 가장 늦은 날짜**로 잡는다. 회의 날짜처럼 이른 값을 쓰면
    # 정산 주차가 그 태스크의 기한보다 앞서 버리고, 다음 실행에서 「나중 주차에 다시
    # 올라왔다」로 읽혀 같은 일이 되살아난다 (2026-09-10 실측: 되살림 1건).
    for t in ledger.values():
        if t["state"] == "done" and not t.get("done_at"):
            known = [when_date((t.get("source") or {}).get("when")),
                     when_date(t.get("due")), when_date(t.get("opened_at"))]
            t["done_at"] = max([k for k in known if k] or [today.isoformat()])

    # ---- 주간 정산 — 지난 주 안에 끝난 일은 보고를 거쳤다고 보고 내린다
    settle_now = can_settle(today, dt.datetime.now())
    settled = []
    for tid, t in (list(ledger.items()) if settle_now else []):
        if t["state"] != "done" or t.get("archived_at"):
            continue
        done = t.get("done_at")
        if not done:
            continue
        try:
            d = dt.date.fromisoformat(done[:10])
        except ValueError:
            continue
        if tuesday_of(d) < this_tue:          # 이번 주 것은 아직 보고 전이라 남긴다
            t["reported_at"] = tuesday_of(d).isoformat()
            t["archived_at"] = today.isoformat()
            settled.append(t)
            ledger.pop(tid)
            ev = {"at": today.isoformat(), "task_id": tid, "what": "정산·아카이브",
                  "state": "done", "title": t["title"][:80]}
            key = (ev["at"], ev["task_id"], ev["what"])
            if key not in seen_events:
                events.append(ev)
                seen_events.add(key)

    if settled:
        wk = f"{this_tue.isocalendar().year}-W{this_tue.isocalendar().week:02d}"
        f = ARCHIVE / f"{wk}.jsonl"
        prev = {t["task_id"]: t for t in read(f)}
        prev.update({t["task_id"]: t for t in settled})
        write(f, list(prev.values()))

    # ---- 확인 필요 — 기한이 지났는데 그 뒤로 아무 증거도 없는 것
    stale = 0
    for t in ledger.values():
        t.pop("needs_check", None)
        if t["state"] not in ("planned", "blocked") or not t.get("due"):
            continue
        try:
            due = dt.date.fromisoformat(t["due"][:10])
        except ValueError:
            continue
        if (today - due).days < STALE_DAYS:
            continue
        # 주의(2026-09-10): 회의록 액션의 when 은 `WM-2026-08-11` 이라 잘라도 `WM-2026-08`
        # 이고, 문자열 비교에서 어떤 날짜보다도 크다. 그래서 action_ledger 행은 기한이
        # 지나도 확인 필요가 붙지 않는다. **고치면 확인 필요가 4건에서 15건으로 뛰고
        # 봇 알림이 나가므로**, R4(마무리된 일정 삭제)를 참석형 일정에 적용할지
        # Roy 판단을 받은 뒤에 함께 고친다. 지금 조용히 고치면 보드가 더 시끄러워진다.
        latest = max([str(e.get("when") or "")[:10] for e in t.get("evidence", [])] or [""])
        if latest and latest > t["due"][:10]:
            continue                       # 기한 뒤에 소식이 있었으면 묻지 않는다
        t["needs_check"] = f'기한 {t["due"][5:]} 이후 소식 없음'
        stale += 1

    # 이미 들어와 있던 것도 걷어낸다
    for tid, t in list(ledger.items()):
        if EXCLUDE.search(t.get("title", "")):
            ledger.pop(tid)
            dropped += 1

    active = sorted(ledger.values(),
                    key=lambda t: (RANK.get(t["state"], 0), t.get("due") or "9999"))
    write(LEDGER, active)
    write(EVENTS, events)

    by = {}
    for t in active:
        by[t["state"]] = by.get(t["state"], 0) + 1
    print(f"· 원장 {len(active)}건 — " + " · ".join(f"{k} {v}" for k, v in sorted(by.items())))
    if not settle_now:
        print(f"  정산 보류 — 주간회의 전 (화 {SETTLE_HOUR}시 이후 내려간다)")
    print(f"  신규 {added} · 갱신 {updated} · 정산 {len(settled)} · 되살림 {revived} · 정산분 재유입 차단 {skipped} · 보드 제외 {dropped}")
    print(f"  이슈 {sum(1 for t in active if t.get('issue'))} · "
          f"담당 있음 {sum(1 for t in active if t.get('owner'))} · "
          f"기한 있음 {sum(1 for t in active if t.get('due'))} · "
          f"확인 필요 {sum(1 for t in active if t.get('needs_check'))}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
