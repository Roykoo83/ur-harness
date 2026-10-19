#!/usr/bin/env python3
"""결정이 필요한 건을 운영자에게 1:1로 알린다.

    python3 scripts/tasks/notify.py [--dry]

보드는 **당겨 봐야 보인다.** 대표님 판단이 필요한 항목이 새로 생겼는데 아무도 열지 않으면
그건 없는 것과 같다. 그래서 새로 생긴 것만 밀어 준다.

**이미 알린 건은 다시 알리지 않는다.** 3시간마다 같은 목록을 보내면 알림 피로로 통째로
무시하게 되고, 그러면 정작 새 건이 왔을 때도 안 열어 본다. (터널 가드에서 겪은 것과 같다)

운영자 지시 2026-08-24 — "의사결정이 필요한 내용이 있다면 나에게 알려줘야지"
"""
from __future__ import annotations

import datetime as dt
import json
import os
import sys
from pathlib import Path

PROJ = Path(__file__).resolve().parents[2]
REPO = PROJ.parents[1]
LEDGER = PROJ / "data" / "tasks" / "ledger.jsonl"
STATE = PROJ / "data" / "tasks" / "notified.json"
NW = REPO / "projects" / "nworks-agent"
DECISION = {"대표님컨펌"}



def _alert_target():
    """알림 대상 → ("user"|"channel", id). 코드에 사람을 박지 않는다.

    설정의 `operator.account` 하나로 조직도를 조회해 1:1 대상을 얻고,
    못 찾으면 부서 채널로 돌린다. 둘 다 없으면 None — 조용히 사라지지 않게
    부르는 쪽이 로그에 남긴다.
    """
    import sys as _s
    from pathlib import Path as _P
    _s.path.insert(0, str(_P(__file__).resolve().parents[2] / "core"))
    from lib.identity import alert_target                        # noqa: PLC0415
    return alert_target()


def read_ledger() -> list[dict]:
    if not LEDGER.exists():
        return []
    return [json.loads(x) for x in LEDGER.read_text(encoding="utf-8").splitlines()
            if x.strip()]


def load_state() -> dict:
    try:
        return json.loads(STATE.read_text(encoding="utf-8"))
    except Exception:
        return {}


def send(text: str) -> bool:
    sys.path.insert(0, str(NW / "scripts"))
    try:
        from nworks.client import NworksClient
        from nworks.config import load_config
        from nworks.domains import bot_send_text
        tgt = _alert_target()
        if not tgt:
            return
        kw = {"user_id": tgt[1]} if tgt[0] == "user" else {"channel_id": tgt[1]}
        bot_send_text(NworksClient(), load_config()["bot_id"], text, **kw)
        return True
    except Exception as e:
        print(f"발송 실패: {str(e)[:160]}")
        return False


def line(t: dict) -> str:
    bits = []
    if t.get("project_id"):
        bits.append(t["project_id"])
    if t.get("due"):
        bits.append(f'기한 {t["due"][5:]}')
    head = f'[{" · ".join(bits)}] ' if bits else ""
    return f'· {head}{t["title"][:150]}'


def main(dry: bool = False) -> int:
    tasks = read_ledger()
    if not tasks:
        print("원장이 비어 있다 — 알리지 않는다")
        return 0

    def needs(t):
        return (t.get("issue") or {}).get("needs")

    decide = [t for t in tasks if needs(t) in DECISION]
    check = [t for t in tasks if t.get("needs_check")]

    state = load_state()
    sent_ids = set(state.get("sent", []))
    new_dec = [t for t in decide if t["task_id"] not in sent_ids]
    new_chk = [t for t in check if t["task_id"] not in sent_ids]

    if not new_dec and not new_chk:
        print(f"새 건 없음 — 결정 {len(decide)} · 확인 {len(check)} (모두 기알림)")
        return 0

    parts = ["🔔 운영 OS — 판단이 필요한 건이 있습니다"]
    if new_dec:
        parts.append(f"\n■ 대표님 결정 필요 (신규 {len(new_dec)}건 / 전체 {len(decide)}건)")
        parts += [line(t) for t in new_dec[:8]]
        if len(new_dec) > 8:
            parts.append(f"  … 외 {len(new_dec) - 8}건")
    if new_chk:
        parts.append(f"\n■ 확인 필요 — 기한이 지났는데 소식이 없습니다 (신규 {len(new_chk)}건)")
        parts += [f'· {t["title"][:120]} ({t.get("needs_check")})' for t in new_chk[:5]]
    parts.append("\n전체 목록 → SSOT > {{DEPT_ROOT}} > 50_대시보드 > 10_운영OS "
                 "> 태스크_상세.html")
    text = "\n".join(parts)

    if dry:
        print(text)
        return 0
    if not send(text):
        return 1

    # **보낸 뒤에 기록한다.** 먼저 기록하면 발송이 실패했을 때 그 건은 영영 안 나간다.
    state["sent"] = sorted(sent_ids | {t["task_id"] for t in new_dec + new_chk})
    state["at"] = dt.datetime.now().isoformat(timespec="minutes")
    STATE.write_text(json.dumps(state, ensure_ascii=False, indent=1), encoding="utf-8")
    os.chmod(STATE, 0o600)
    print(f"· 알림 발송 — 결정 신규 {len(new_dec)} · 확인 신규 {len(new_chk)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main("--dry" in sys.argv))
