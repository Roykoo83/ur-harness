# UR Harness 설치 — Windows 10/11 · Windows Server 2019+
#
#   powershell -ExecutionPolicy Bypass -File install\bootstrap.ps1
#   powershell -ExecutionPolicy Bypass -File install\bootstrap.ps1 -Check
#
# 이 스크립트는 **설치를 강요하지 않는다.** 없는 것을 알려주고, 동의를 받아 설치한다.
# 관리자 권한은 필요하지 않다 — 예약 작업도 사용자 수준으로 등록한다.

param([switch]$Check)

$ErrorActionPreference = "Continue"
Set-Location (Join-Path $PSScriptRoot "..")
$Repo = (Get-Location).Path
$Missing = @()

function Say  ($m) { Write-Host "`n$m" -ForegroundColor White }
function OK   ($m) { Write-Host "  [OK] $m"   -ForegroundColor Green }
function No   ($m) { Write-Host "  [--] $m"   -ForegroundColor Red }
function Warn ($m) { Write-Host "  [!]  $m"   -ForegroundColor Yellow }

function Have($name) { $null -ne (Get-Command $name -ErrorAction SilentlyContinue) }

Say "1. 필수 런타임"
if (Have "python") {
    $v = (python -c "import sys;print('.'.join(map(str,sys.version_info[:2])))" 2>$null)
    python -c "import sys;sys.exit(0 if sys.version_info>=(3,10) else 1)" 2>$null
    if ($LASTEXITCODE -eq 0) { OK "python $v" }
    else { No "python $v — 3.10 이상이 필요하다"; $Missing += "python" }
} else { No "python 없음"; $Missing += "python" }

if (Have "git") { OK "git $((git --version).Split(' ')[2])" } else { No "git 없음"; $Missing += "git" }

Say "2. 드라이브 접근 (필수)"
if (Have "rclone") {
    OK "rclone $((rclone version | Select-Object -First 1).Split(' ')[1])"
    $remotes = (rclone listremotes 2>$null)
    foreach ($r in @("SSOT_ro", "SSOT_rw")) {
        if ($remotes -match "^${r}:") { OK "원격 ${r}: 설정됨" }
        else { Warn "원격 ${r}: 없음 — 아래 4번 참조" }
    }
} else { No "rclone 없음 — 정본 읽기·쓰기에 반드시 필요하다"; $Missing += "rclone" }

Say "3. 선택 런타임 (없으면 해당 기능만 건너뛴다)"
if (Have "node")        { OK "node $(node -v)" }  else { Warn "node 없음 — 다이어그램(archify) 생성 불가" }
if (Have "ffmpeg")      { OK "ffmpeg" }           else { Warn "ffmpeg 없음 — 회의 녹음 전사 불가" }
if (Have "cloudflared") { OK "cloudflared" }      else { Warn "cloudflared 없음 — 봇 수신 콜백 불가 (발신은 가능)" }

Say "4. 설정"
if (Test-Path "config\harness.yaml") { OK "config\harness.yaml 있음" }
else { No "config\harness.yaml 없음 — example 을 복사해 채운다"; }
if (Test-Path "$env:USERPROFILE\.nworks\config.json") { OK "그룹웨어 설정(~\.nworks) 있음" }
else { Warn "그룹웨어 설정 없음 — docs\03_봇_게이트웨이_연동.md 참조" }

if ($Missing.Count -gt 0) {
    Say "설치가 필요한 것: $($Missing -join ', ')"
    Write-Host @"
    winget (Windows 10 1809+ 기본 포함):
      winget install -e --id Python.Python.3.12
      winget install -e --id Git.Git
      winget install -e --id Rclone.Rclone
      winget install -e --id Gyan.FFmpeg          # 선택 (전사)
      winget install -e --id OpenJS.NodeJS.LTS    # 선택 (다이어그램)

    winget 이 없으면 각 공식 설치본을 받는다:
      python  https://www.python.org/downloads/windows/   (설치 시 "Add to PATH" 체크)
      rclone  https://rclone.org/downloads/
"@
    if ($Check) { exit 1 }
    $a = Read-Host "  위 명령을 지금 실행할까요? [y/N]"
    if ($a -eq "y" -or $a -eq "Y") {
        if (Have "winget") {
            winget install -e --id Python.Python.3.12 --accept-source-agreements --accept-package-agreements
            winget install -e --id Git.Git           --accept-source-agreements --accept-package-agreements
            winget install -e --id Rclone.Rclone     --accept-source-agreements --accept-package-agreements
            Warn "새로 설치한 것은 PATH 에 반영되도록 PowerShell 을 다시 연 뒤 이 스크립트를 다시 돌린다"
        } else { No "winget 이 없다 — 위 링크에서 직접 설치한다" }
        exit 1
    } else { Write-Host "  설치하지 않았습니다. 위를 직접 설치한 뒤 다시 돌리세요."; exit 1 }
}

if ($Check) { Say "점검만 수행했습니다"; exit 0 }

Say "5. 파이썬 패키지"
python -m pip install --user -q -r install\requirements.txt
if ($LASTEXITCODE -eq 0) { OK "requirements 설치 완료" } else { No "pip 실패" }

Say "6. 작업공간"
$ws = (python -c "import sys;sys.path.insert(0,'core');from lib.config import paths;print(paths.workspace)" 2>$null)
if ($ws) {
    foreach ($d in @("state","logs","output")) { New-Item -ItemType Directory -Force -Path (Join-Path $ws $d) | Out-Null }
    OK "작업공간 $ws"
} else { Warn "설정을 먼저 채우세요" }

Say "7. 장기 실행 설정 (선택이지만 권장)"
Write-Host @"
    예약 작업이 로그아웃 후에도 돌게 하려면, 등록한 각 작업에서
    '사용자의 로그온 여부에 관계없이 실행' 을 켠다. 그러면 계정 암호를 한 번 묻는다.
    서버(Windows Server)라면 이 설정이 사실상 필수다.
"@

Say "다음 단계"
Write-Host @"
    1) copy config\harness.example.yaml config\harness.yaml   그리고 값을 채운다
    2) rclone config        # SSOT_ro · SSOT_rw 원격을 만든다 (docs\01_관리자_온보딩.md)
    3) python install\configure.py --all
    4) 예약 작업 등록 — configure 가 출력한 명령을 그대로 실행
    5) python core\gate_check.py        # 정합성 확인
"@
