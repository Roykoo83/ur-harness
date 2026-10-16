#!/bin/bash
# 원장 회차 — 아침 08 · 점심 13 · 저녁 17 (Roy 설계 2026-09-11)
#
# U 를 먼저 훑고 원장을 만든다. 순서가 중요하다 — U 갱신 없이 원장을 만들면
# 오늘 만든 과제 파일이 원장에 안 들어간다.
# 실패해도 다음 단계를 막지 않는다. 원장이 한 스트림 때문에 통째로 멈추면
# 화면이 더 낡아진다.
set -uo pipefail
# 자동 실행에는 PATH 가 없다. 절대 경로로 고정한다 (오답노트 E05).
export PATH="/opt/homebrew/bin:/usr/local/bin:$PATH"
RCLONE="$(command -v rclone || echo /opt/homebrew/bin/rclone)"
PROJ="$(cd "$(dirname "$0")/../.." && pwd)"
PY="${PYTHON_BIN:-$HOME/miniconda3/bin/python3}"
command -v "$PY" >/dev/null 2>&1 || PY=python3
LOG="$PROJ/workspace/재정비/로그/회차.log"
mkdir -p "$(dirname "$LOG")"
say(){ echo "[$(date '+%m-%d %H:%M:%S')] $*" >> "$LOG"; }

say "===== 회차 시작 ====="
bash "$PROJ/scripts/원장/U스캔.sh" 21d >> "$LOG" 2>&1 || say "U 스캔 실패 (직전 결과로 진행)"
"$PY" "$PROJ/scripts/원장/원장생성.py"      >> "$LOG" 2>&1 || say "!! 원장 생성 실패"
"$PY" "$PROJ/scripts/원장/분류요청생성.py"   >> "$LOG" 2>&1 || say "분류 요청 생성 실패 (비치명)"
"$PY" "$PROJ/scripts/원장/온톨로지수확.py"   >> "$LOG" 2>&1 || say "온톨로지 수확 실패 (비치명)"

# SSOT 의 데이터 자리에 놓는다. 대시보드가 `script src` 로 읽는다.
# **사람 폴더(KSH/)에는 쓰지 않는다.** 같은 파일을 둘이 쓰면 사본이 생긴다 (2026-09-07 사고).
#
# **마운트로 쓰지 않고 rclone 으로 올린다.** 구글 드라이브 마운트는 macOS 샌드박스가
# 파일 단위로 접근을 허가한다(com.apple.macl). 터미널에서 만진 파일에는 허가가 붙어
# 되지만, launchd 에이전트가 처음 보는 파일에는 안 붙어 Operation not permitted 가 난다.
# 그래서 9/15~9/21 열여덟 회차가 자동에서만 조용히 실패했다 (오답노트 E05 3회차).
# rclone 은 네트워크로 드라이브 API 를 부르므로 이 문제가 없다.
#
# 이 폴더의 쓰기 경로는 **rclone 하나뿐이다.** 마운트 cp 와 섞지 않는다.
DST="SSOT_rw:{{DEPT_ROOT}}/50_대시보드/10_운영OS/_데이터"
ok=1
for src in "$PROJ/output/원장/최신.js" "$PROJ/output/원장/최신.md" \
           "$(dirname "$0")/화면/연동.js"; do
  b="$(basename "$src")"
  if [ ! -f "$src" ]; then say "  배포 원본 없음: $src"; ok=0; continue; fi
  # 파이프의 종료코드는 **마지막 명령의 것**이다. `| grep -v NOTICE` 를 붙였더니
  # rclone 이 성공해도 grep 이 「걸린 줄 없음(1)」을 돌려주어 실패로 읽혔다.
  # rclone 자신의 종료코드를 따로 잡는다.
  err="$("$RCLONE" copyto "$src" "$DST/$b" 2>&1)"; rc=$?
  [ $rc -eq 0 ] || { say "  $b 업로드 실패($rc): $(echo "$err" | grep -v NOTICE | head -2)"; ok=0; }
done

# **올렸다고 믿지 않고 서버에 물어 확인한다** (오답노트: 도착은 표식이 아니라 대상에게 묻는다).
if [ "$ok" = 1 ]; then
  want=$(/usr/bin/stat -f%z "$PROJ/output/원장/최신.js")
  got=$("$RCLONE" size --json "$DST/최신.js" 2>/dev/null | sed -n 's/.*"bytes":\([0-9]*\).*/\1/p')
  if [ "$want" = "$got" ]; then say "SSOT 데이터 자리 갱신 (${got}B 도착 확인)"
  else say "SSOT 업로드는 됐으나 대조 불일치: 원본 ${want}B / 서버 ${got:-없음}B"; fi
else
  say "SSOT 배포 실패 (로컬 원장은 최신)"
fi
say "===== 회차 종료 ====="
