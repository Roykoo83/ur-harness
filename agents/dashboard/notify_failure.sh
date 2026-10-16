#!/bin/bash
# 주간 사이클 실패를 Roy 1:1 로 알린다. nworks-agent 의 봇 발송 경로를 재사용한다.
# (Roy 승인 발송 유형 — 사이클 이상 경고, 2026-08-05)
MSG="${1:-주간 대시보드 사이클 실패}"
NW="$HOME/{{WORKSPACE_NAME}}/projects/nworks-agent"
exec python3 - "$MSG" <<'PY'
import sys, pathlib
msg = sys.argv[1]
proj = pathlib.Path.home() / "{{WORKSPACE_NAME}}/projects/nworks-agent"
sys.path.insert(0, str(proj / "scripts"))
try:
    from nworks.client import NworksClient
    from nworks.config import load_config
    from nworks.domains import bot_send_text
    ROY = "c50aed6d-af42-42ef-16a1-03f2f66c3ae7"
    bot_send_text(NworksClient(), load_config()["bot_id"],
                  f"[Urban AX Bot·경고] {msg}\n로그: urban-weekly-dashboard/workspace/weekly.log",
                  user_id=ROY)
except Exception as e:
    print(f"경고 발송 실패: {e}", file=sys.stderr)
PY
