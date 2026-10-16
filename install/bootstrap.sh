#!/usr/bin/env bash
# UR Harness 설치 — Ubuntu 22.04+ / Debian 12+
#
#   bash install/bootstrap.sh            점검하고 필요한 것을 설치한다
#   bash install/bootstrap.sh --check    설치하지 않고 점검만 한다
#
# 이 스크립트는 **설치를 강요하지 않는다.** 없는 것을 알려주고, 동의를 받아 설치한다.
set -uo pipefail
cd "$(dirname "$0")/.." || exit 1
REPO="$PWD"
CHECK_ONLY=0; [ "${1:-}" = "--check" ] && CHECK_ONLY=1
ok(){ printf "  \033[32m✓\033[0m %s\n" "$1"; }
no(){ printf "  \033[31m✗\033[0m %s\n" "$1"; }
warn(){ printf "  \033[33m!\033[0m %s\n" "$1"; }
MISSING=()

say(){ printf "\n\033[1m%s\033[0m\n" "$1"; }

say "1. 필수 런타임"
if command -v python3 >/dev/null 2>&1; then
  PYV=$(python3 -c 'import sys;print(".".join(map(str,sys.version_info[:2])))')
  if python3 -c 'import sys;sys.exit(0 if sys.version_info>=(3,10) else 1)'; then
    ok "python3 $PYV"
  else
    no "python3 $PYV — 3.10 이상이 필요하다"; MISSING+=("python3.10+")
  fi
else
  no "python3 없음"; MISSING+=("python3")
fi
command -v git >/dev/null 2>&1 && ok "git $(git --version | awk '{print $3}')" || { no "git 없음"; MISSING+=("git"); }

say "2. 드라이브 접근 (필수)"
if command -v rclone >/dev/null 2>&1; then
  ok "rclone $(rclone version 2>/dev/null | head -1 | awk '{print $2}')"
  for r in SSOT_ro SSOT_rw; do
    rclone listremotes 2>/dev/null | grep -q "^${r}:" && ok "원격 ${r}: 설정됨" || warn "원격 ${r}: 없음 — 아래 4번 참조"
  done
else
  no "rclone 없음 — 정본 읽기·쓰기에 반드시 필요하다"; MISSING+=("rclone")
fi

say "3. 선택 런타임 (없으면 해당 기능만 건너뛴다)"
command -v node >/dev/null 2>&1 && ok "node $(node -v)" || warn "node 없음 — 다이어그램(archify) 생성 불가"
command -v ffmpeg >/dev/null 2>&1 && ok "ffmpeg" || warn "ffmpeg 없음 — 회의 녹음 전사 불가"
command -v cloudflared >/dev/null 2>&1 && ok "cloudflared" || warn "cloudflared 없음 — 봇 수신 콜백 불가 (발신은 가능)"

say "4. 설정"
[ -f config/harness.yaml ] && ok "config/harness.yaml 있음" || no "config/harness.yaml 없음 — example 을 복사해 채운다"
[ -f "$HOME/.nworks/config.json" ] && ok "그룹웨어 설정(~/.nworks) 있음" || warn "그룹웨어 설정 없음 — docs/02_hermes_bot.md 참조"

if [ ${#MISSING[@]} -gt 0 ]; then
  say "설치가 필요한 것: ${MISSING[*]}"
  cat <<'EOT'
  Ubuntu / Debian:
    sudo apt update
    sudo apt install -y python3 python3-pip python3-venv git ffmpeg
    # rclone (공식 스크립트)
    curl https://rclone.org/install.sh | sudo bash
    # node (선택)
    curl -fsSL https://deb.nodesource.com/setup_22.x | sudo -E bash - && sudo apt install -y nodejs
EOT
  [ "$CHECK_ONLY" = "1" ] && exit 1
  read -r -p "  위 명령을 지금 실행할까요? [y/N] " a
  if [ "${a:-N}" = "y" ] || [ "${a:-N}" = "Y" ]; then
    sudo apt update && sudo apt install -y python3 python3-pip python3-venv git ffmpeg || warn "apt 실패"
    command -v rclone >/dev/null 2>&1 || curl https://rclone.org/install.sh | sudo bash
  else
    echo "  설치하지 않았습니다. 위 명령을 직접 실행한 뒤 다시 돌리세요."; exit 1
  fi
fi

[ "$CHECK_ONLY" = "1" ] && { say "점검만 수행했습니다"; exit 0; }

say "5. 파이썬 패키지"
python3 -m pip install --user -q -r install/requirements.txt && ok "requirements 설치 완료" || no "pip 실패"

say "6. 작업공간"
WS=$(python3 -c "import sys;sys.path.insert(0,'core');from lib.config import paths;print(paths.workspace)" 2>/dev/null)
if [ -n "$WS" ]; then mkdir -p "$WS"/{state,logs,output} && ok "작업공간 $WS"; else warn "설정을 먼저 채우세요"; fi

say "다음 단계"
cat <<'EOT'
  1) cp config/harness.example.yaml config/harness.yaml  &&  값을 채운다
  2) rclone config      # SSOT_ro · SSOT_rw 원격을 만든다 (docs/01_admin.md)
  3) python3 install/configure.py --all
  4) systemd 유닛 설치 — configure 가 출력한 명령을 그대로 실행
  5) python3 core/gate_check.py      # 정합성 확인
EOT
