#!/bin/bash
# U 드라이브 최근 변경 갱신 — 원장 회차 직전에 돈다.
#
# 전수 인벤토리(6,724파일)는 9/11 스냅샷으로 두고, **최근 창만 다시 훑는다.**
# 전수를 매번 돌면 느리고, 스냅샷만 쓰면 오늘 만든 파일이 영영 안 보인다.
# U 는 읽기 전용이다. 어떤 파일도 만들지 않는다.
set -uo pipefail
# 자동 실행에는 PATH 가 없다. 절대 경로로 고정한다 (오답노트 E05).
export PATH="/opt/homebrew/bin:/usr/local/bin:$PATH"
RCLONE="$(command -v rclone || echo /opt/homebrew/bin/rclone)"
PROJ="$(cd "$(dirname "$0")/../.." && pwd)"
OUT="$PROJ/workspace/재정비/인벤토리/최근"
LOG="$PROJ/workspace/재정비/로그/U스캔.log"
TD="--drive-team-drive {{SOURCE_DRIVE_ID}}"
AGE="${1:-21d}"
mkdir -p "$OUT" "$(dirname "$LOG")"
say(){ echo "[$(date '+%m-%d %H:%M:%S')] $*" >> "$LOG"; }

say "===== U 최근 ${AGE} 갱신 시작 ====="
/usr/bin/python3 - "$PROJ" > /tmp/_u_paths.tsv <<'PY'
import json,sys
d=json.load(open(f"{sys.argv[1]}/scripts/원장/과제정의.json",encoding="utf-8"))
for p in d["과제"]:
    if p.get("u"): print(f"{p['key']}\t{p['u']}")
PY

ok=0; fail=0
while IFS=$'\t' read -r key path; do
  f="$OUT/${key}.tsv"
  if "$RCLONE" lsf $TD "gdrive:$path" -R --files-only --max-age "$AGE" \
       --format "tsp" --separator $'\t' > "$f.tmp" 2>>"$LOG"; then
    mv "$f.tmp" "$f"; n=$(wc -l < "$f" | tr -d ' ')
    say "  ${key}: ${n}건"; ok=$((ok+1))
  else
    rm -f "$f.tmp"; say "  !! ${key} 조회 실패 (직전 결과 유지)"; fail=$((fail+1))
  fi
  sleep 3
done < /tmp/_u_paths.tsv
rm -f /tmp/_u_paths.tsv
say "===== 완료 · 성공 ${ok} · 실패 ${fail} ====="
