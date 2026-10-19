#!/bin/bash
# 주간 사이클 실패를 운영자 1:1 로 알린다. nworks-agent 의 봇 발송 경로를 재사용한다.
# (운영자 승인 발송 유형 — 사이클 이상 경고, 2026-08-05)
MSG="${1:-주간 대시보드 사이클 실패}"
NW="$(cd "$(dirname "$0")/../.." && pwd)"   # 저장소 루트
exec python3 - "$MSG" <<'PY'
import sys, pathlib
msg = sys.argv[1]
root = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(root / "agents"))
sys.path.insert(0, str(root / "core"))
try:
    from nworks.client import NworksClient
    from nworks.config import load_config
    from nworks.domains import bot_send_text
    from lib.identity import alert_target
    tgt = alert_target()
    if not tgt:
        raise SystemExit("알림 대상을 찾지 못했다 — 설정 operator.account 를 확인한다")
    kw = {"user_id": tgt[1]} if tgt[0] == "user" else {"channel_id": tgt[1]}
    bot_send_text(NworksClient(), load_config()["bot_id"],
                  f"[경고] {msg}\n로그: 주간 대시보드 작업로그", **kw)
except Exception as e:
    print(f"경고 발송 실패: {e}", file=sys.stderr)
PY
