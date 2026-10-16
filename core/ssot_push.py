#!/usr/bin/env python3
"""ssot-push — **퇴역** (2026-09-07, M 안)

이 스크립트는 마운트에 있는 파일을 rclone(API)으로 다시 올렸다. DriveFS 도 같은 파일을 올리므로
Drive 에 같은 이름이 두 개 생기고, 그 사본이 마운트를 EDEADLK 로 잠갔다 (9/7 오전 33건·오후 9경로).
M 안에서는 사람 구역은 마운트가, 기계 구역은 API 가 **각각 단독으로** 쓴다. 재업로드가 설 자리가 없다.

2026-09-07 14:58 에 누군가 이 스크립트를 직접 실행했다 (launchd 아님 — .out 갱신 없음).
그래서 파일을 남기되 실행을 막는다. 원본은 ssot_push.py.retired.

마운트↔클라우드 정합성이 걱정되면:  python3 scripts/watchdog.py --dry-run
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
from lib import hb

hb.blocked("ssot-push",
           "퇴역 — 마운트 재업로드가 DriveFS 와 경합해 Drive 동명 사본을 만들었다 (2026-09-07)",
           needs="없음. M 안(구역표)에서는 필요하지 않다. 정합성 확인은 watchdog.py")
print(__doc__, file=sys.stderr)
sys.exit(2)
