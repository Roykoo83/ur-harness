#!/usr/bin/env python3
"""실행 파일 찾기 — OS 마다 다른 설치 위치를 한 곳에서 푼다.

왜 있나
  `shutil.which()` 가 실패할 때 쓰는 폴백을 파일마다 따로 적어 두면,
  그 폴백이 **만든 사람의 기계 경로**가 된다. 다른 OS·다른 담당자에게는 없는 경로다.
  2026-10-21 실측: macOS 홈브루 설치 경로가 네 파일에 흩어져 있었다 — 그 OS 전용이다.

  그리고 「없는데 있다고 치는」 폴백은 더 나쁘다. 가드가 도구를 못 찾았을 때
  통과로 처리하면 그 가드는 영구히 꺼진다 — 이미 한 번 겪었다.

쓰는 법
    from lib.runtime import find_exe
    rclone = find_exe("rclone")          # 못 찾으면 None. 「없음」을 숨기지 않는다
"""
from __future__ import annotations

import os
import platform
import shutil
from pathlib import Path

# OS 별 흔한 설치 위치. PATH 에서 못 찾았을 때만 본다.
_CANDIDATES = {
    "darwin": ("/opt/homebrew/bin", "/usr/local/bin", "/usr/bin"),
    "linux": ("/usr/local/bin", "/usr/bin", "/bin", "/snap/bin",
              str(Path.home() / ".local/bin")),
    "windows": (r"C:\Program Files", r"C:\Program Files (x86)",
                str(Path.home() / "AppData/Local/Programs")),
}


def find_exe(name: str, *, env_var: str | None = None) -> str | None:
    """실행 파일 경로. **못 찾으면 None** — 가짜 경로를 돌려주지 않는다.

    순서: 환경변수 → PATH → OS 별 흔한 위치.
    """
    if env_var and (v := os.environ.get(env_var)):
        if Path(v).is_file():
            return v
    if (p := shutil.which(name)):
        return p
    osname = platform.system().lower()
    exe = f"{name}.exe" if osname == "windows" else name
    for d in _CANDIDATES.get(osname, ()):
        c = Path(d) / exe
        if c.is_file():
            return str(c)
        # 윈도우는 한 단계 더 들어가는 경우가 많다 (C:\Program Files\rclone\rclone.exe)
        if osname == "windows":
            c = Path(d) / name / exe
            if c.is_file():
                return str(c)
    return None


def require_exe(name: str, *, env_var: str | None = None, why: str = "") -> str:
    """없으면 멈춘다. 「작동 불가」를 「통과」로 만들지 않기 위해서다."""
    p = find_exe(name, env_var=env_var)
    if p:
        return p
    raise SystemExit(
        f"{name} 을 찾지 못했다{' — ' + why if why else ''}.\n"
        f"  PATH 에 넣거나 설치한다. 환경변수로 지정하려면 "
        f"{env_var or name.upper() + '_BIN'}=<경로>")


def python_exe() -> str:
    """서비스가 쓸 파이썬. 설정이 있으면 그것, 없으면 지금 도는 파이썬."""
    import sys
    try:
        from lib.config import cfg                            # noqa: PLC0415
        if (v := cfg("install.python")):
            return v
    except Exception:                                         # noqa: BLE001
        pass
    return sys.executable or "python3"
