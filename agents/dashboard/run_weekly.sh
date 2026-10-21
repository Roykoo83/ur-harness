#!/bin/bash
# 주간 컨플루언스 수집 → 부서별 대시보드 생성.
# launchd `com.{{ORG_NAME}}.weekly-dashboard` 가 매주 화요일 09:30에 호출한다.
set -uo pipefail

PROJ="$(cd "$(dirname "$0")/../.." && pwd)"   # 저장소 루트 — 코드는 여기 있다
LOG="$PROJ/workspace/weekly.log"
# 특정 파이썬 설치본을 기본값으로 박지 않는다 — 다른 기계에는 없는 경로다.
# PATH 의 python3 를 쓰고, 다르게 쓰려면 PYTHON_BIN 으로 지정한다.
PY="${PYTHON_BIN:-$(command -v python3 || echo python3)}"
command -v "$PY" >/dev/null 2>&1 || PY=python3
mkdir -p "$PROJ/workspace"

log() { echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*" >> "$LOG"; }

log "===== 주간 사이클 시작 ====="

# 토큰 만료 예고. 2026-08-20 에 조용히 만료돼 수집이 멈춘 적이 있다 —
# 만료는 예고 없이 오지 않으므로 남은 날짜를 미리 알린다.
"$PY" - <<'PYEOF' >> "$LOG" 2>&1
import datetime as dt, json, pathlib
p = pathlib.Path.home()/".config/{{WORKSPACE_SLUG}}/confluence.json"
try:
    exp = json.loads(p.read_text(encoding="utf-8")).get("expires")
    if exp:
        left = (dt.date.fromisoformat(exp) - dt.date.today()).days
        if left <= 30:
            print(f"!! Atlassian 토큰 만료 {left}일 남음 ({exp}) — 갱신 절차 R2 "
                  f"(docs/06_운영과_장애대응.md)")
except Exception:
    pass
PYEOF

# 1) 수집 — API 토큰이 있으면 그것만 쓴다(브라우저 없는 환경 대비).
#    토큰이 없으면 브라우저 세션 폴백을 허용하되 실패를 기록한다.
if [ -f "$HOME/.config/{{WORKSPACE_SLUG}}/confluence.json" ] || [ -n "${CONFLUENCE_TOKEN:-}" ]; then
  MODE="--no-browser"
  log "인증: API 토큰"
else
  MODE=""
  log "인증: 브라우저 세션 폴백 (API 토큰 권장 — README 참조)"
fi

# 재시도가 필요한 이유 — 이 작업은 **주 1회**라 한 번 실패하면 다음 기회가 일주일 뒤다.
# 2026-08-18 09:35 에 몇 분짜리 DNS 장애로 통째로 건너뛰었다. 같은 장애가 실패 알림의
# 발송 경로(봇 API)도 함께 끊어서 아무 연락도 가지 않았다 — 알림은 네트워크 장애에
# 대한 대비책이 되지 못한다. 그래서 조용히 기다렸다가 다시 시도한다.
COLLECTED=0
for WAIT in 0 60 180 300 600; do
  [ "$WAIT" -gt 0 ] && { log "재시도 대기 ${WAIT}초"; sleep "$WAIT"; }
  if "$PY" "$PROJ/scripts/collect_weekly.py" --weeks 4 $MODE >> "$LOG" 2>&1; then
    COLLECTED=1
    [ "$WAIT" -gt 0 ] && log "수집 성공 (재시도 후)"
    break
  fi
  log "!! 수집 실패 — 재시도 예정"
done

if [ "$COLLECTED" -eq 0 ]; then
  log "!! 수집 최종 실패 (5회) — 대시보드 생성을 건너뛴다"
  "$PROJ/scripts/notify_failure.sh" "컨플루언스 주간 수집 실패 (5회 재시도 후)" 2>/dev/null || true
  exit 1
fi

# 2) 부서별 대시보드 생성
if ! "$PY" "$PROJ/scripts/build_dashboards.py" >> "$LOG" 2>&1; then
  log "!! 대시보드 생성 실패"
  "$PROJ/scripts/notify_failure.sh" "부서 대시보드 생성 실패" 2>/dev/null || true
  exit 1
fi

# 3) RPBD 운영 OS 대시보드도 같은 사이클에서 최신화 (SSOT 파이프라인)
RPBD="$(cd "$(dirname "$0")/../.." && pwd)"   # 저장소 루트
if [ -f "$RPBD/src/rpbd/pipeline.py" ]; then
  (cd "$RPBD" && PYTHONPATH="$RPBD/src" "$PY" -m rpbd.pipeline >> "$LOG" 2>&1) \
    && log "RPBD 파이프라인 갱신 완료" \
    || log "RPBD 파이프라인 갱신 실패 (부서 대시보드는 정상)"
fi

log "===== 완료 ====="
exit 0
