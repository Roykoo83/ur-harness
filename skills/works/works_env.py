"""works 워크플로우의 환경 탐지 — macOS·Windows 공용.

`works.py` 와 `new-project.py` 가 함께 쓴다. 두 스크립트는 같은 폴더에 배포되므로
`import works_env` 로 불린다. 이 파일만 고치면 두 스크립트의 경로 처리가 함께 바뀐다.

사람마다 OS 도 계정도 다르다. 경로를 고정하지 않고 찾는다.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

SSOT_NAME = "{{SSOT_DRIVE}}"
# 구글 드라이브의 공유 드라이브 폴더명은 계정 표시 언어에 따라 다르다
SHARED_DIR_NAMES = ("공유 드라이브", "Shared drives")


def setup_stdout() -> None:
    """한글 출력이 Windows 콘솔(cp949)에서 깨지지 않게 한다."""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")  # type: ignore[union-attr]
        except (AttributeError, OSError, ValueError):
            pass


def ssot_candidates() -> list[Path]:
    """OS 별 SSOT 후보 경로. 앞쪽이 더 유력하다."""
    home = Path.home()
    out: list[Path] = []

    if sys.platform == "darwin":
        cloud = home / "Library" / "CloudStorage"
        if cloud.is_dir():
            for base in sorted(cloud.glob("GoogleDrive-*")):
                out += [base / s / SSOT_NAME for s in SHARED_DIR_NAMES]
    elif os.name == "nt":
        # 스트리밍 모드는 드라이브 문자로 붙는다 (기본 G:, 사용자가 바꿀 수 있다).
        # 없는 드라이브는 exists() 가 즉시 False 라 훑어도 느리지 않다.
        for letter in "GHIJKLMNOPQRSTUVWXYZDEF":
            root = Path(f"{letter}:/")
            out += [root / s / SSOT_NAME for s in SHARED_DIR_NAMES]
        # 미러링 모드는 홈 아래로 들어온다
        gd = home / "Google Drive"
        out += [gd / s / SSOT_NAME for s in SHARED_DIR_NAMES] + [gd / SSOT_NAME]
    else:
        gd = home / "Google Drive"
        out += [gd / s / SSOT_NAME for s in SHARED_DIR_NAMES] + [gd / SSOT_NAME]

    return out


def find_ssot() -> Path:
    """SSOT 마운트를 찾는다.

    순서: 환경변수 `UR_SSOT` >> OS 별 후보 경로.
    못 찾으면 마지막 후보를 그대로 돌려주고, 호출부가 `is_dir()` 로 걸러 안내한다.
    """
    env = os.environ.get("UR_SSOT")
    if env:
        return Path(env)
    cands = ssot_candidates()
    for p in cands:
        try:
            if p.is_dir():
                return p
        except OSError:
            continue
    return cands[0] if cands else Path.home() / SSOT_NAME


def ssot_help(found: Path) -> str:
    """SSOT 를 못 찾았을 때 사람에게 줄 안내. OS 마다 확인할 것이 다르다."""
    lines = [f"  찾아본 위치: {found}"]
    if os.name == "nt":
        lines += [
            "  확인할 것: ①구글 드라이브(데스크톱용)가 실행 중인지",
            "            ②파일 탐색기에서 `G:` 같은 드라이브에 `공유 드라이브` 가 보이는지",
            "  경로가 다르면 (PowerShell):",
            '    $env:UR_SSOT="G:\\공유 드라이브\\{{SSOT_DRIVE}}"',
        ]
    else:
        lines += [
            "  확인할 것: ①구글 드라이브 데스크톱 앱이 켜져 있는지",
            "            ②`공유 드라이브/{{SSOT_DRIVE}}` 가 보이는지",
            "  경로가 다르면: UR_SSOT='<내 경로>' 를 앞에 붙여 실행하세요",
        ]
    return "\n".join(lines)


def repo_base() -> Path:
    """작업공간(repo). 연구기획사업부 담당자 맥에만 있다. 없으면 관련 절이 비워진다."""
    return Path(os.environ.get("UR_REPO", str(Path.home() / "{{WORKSPACE_NAME}}")))
