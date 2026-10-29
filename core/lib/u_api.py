"""U 공유 드라이브를 **API 로** 읽는다. 마운트를 쓰지 않는다.

왜 API 인가
  마운트는 이 기계의 DriveFS 에 매달려 있다. 업로드가 서면 조용히 어긋나고
  (2026-10-22 실측: 마운트에 쓴 파일이 3시간 넘게 클라우드에 없었다), 다음
  인수인계자의 기계에는 같은 마운트가 아예 없다. 그래서 U 도 SSOT 도 API 로 본다.

왜 스테이징인가
  U 를 읽는 모듈들은 파일을 걸어 다니며 읽는다(컨플루언스 아카이브 4,147건 등).
  파일마다 API 를 때리면 느리고 쿼터를 먹는다. 한 번에 받아 로컬에 펼쳐 두고,
  모듈은 지금처럼 로컬 경로를 걸어 다닌다. 받은 것은 **읽기 전용 사본**이다.

신분
  Roy 계정 OAuth(`U_api`). 서비스계정은 U 공유 드라이브 멤버가 아니다(404).
  멤버로 등록되면 설정의 `u.remote` 만 서비스계정 원격으로 바꾸면 된다.
"""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

from lib.config import cfg, paths


class UnavailableError(RuntimeError):
    """U 를 읽을 수 없다. **추측해서 빈 결과를 돌려주지 않는다** — 빈 결과는
    「변화 없음」으로 읽혀 downstream 이 멀쩡한 것을 지우거나 덮어쓴다."""


def remote() -> str:
    r = cfg("u.remote")
    if not r:
        raise UnavailableError(
            "설정에 u.remote 가 없다. U 를 읽는 rclone 원격 이름을 적는다\n"
            "  예: u:\n        remote: \"U_api\"")
    return r.rstrip(":") + ":"


def _rclone() -> str:
    exe = shutil.which("rclone")
    if not exe:
        raise UnavailableError("rclone 이 없다 — U 는 API 로만 읽는다")
    return exe


def listing(subpath: str = "", *, timeout: int = 600) -> list[str]:
    """U 아래 경로 목록. 내용은 받지 않는다."""
    r = subprocess.run([_rclone(), "lsf", "-R", f"{remote()}{subpath}"],
                       capture_output=True, text=True, timeout=timeout)
    if r.returncode != 0:
        raise UnavailableError(f"목록 실패: {r.stderr.strip()[:200]}")
    return [x for x in r.stdout.splitlines() if x.strip()]


def stage(subpath: str, *, timeout: int = 1800) -> Path:
    """U 의 한 하위 경로를 로컬에 펼쳐 **그 로컬 경로**를 돌려준다.

    모듈은 지금처럼 파일을 걸어 다니면 된다. 바뀐 것은 어디를 걷느냐뿐이다.
    """
    dest = Path(cfg("u.stage") or (paths.state / "u_stage")).expanduser() / subpath
    dest.mkdir(parents=True, exist_ok=True)
    r = subprocess.run([_rclone(), "copy", f"{remote()}{subpath}", str(dest),
                        "--fast-list", "--checkers", "8"],
                       capture_output=True, text=True, timeout=timeout)
    if r.returncode != 0:
        raise UnavailableError(f"받기 실패 ({subpath}): {r.stderr.strip()[:200]}")
    return dest


def push(local: Path | str, subpath: str, *, mirror: bool = False,
         timeout: int = 1800) -> int:
    """로컬 산출물을 U 의 한 경로에 올린다. 돌려주는 값은 전송 건수.

    `mirror=True` 는 **대상에만 있는 파일을 지운다.** 그래서 그냥 허락하지 않는다 —
    설정의 `u.mirror_ok` 에 그 경로가 적혀 있을 때만 지우는 동기화를 쓴다.
    자동화는 자기가 만든 파일만 덮어쓴다는 규칙을 코드로 지키는 것이다.
    적혀 있지 않으면 지우지 않는 복사(copy)로 내려간다.
    """
    ok = [str(x) for x in (cfg("u.mirror_ok") or [])]
    allowed = mirror and any(subpath == o or subpath.startswith(o.rstrip("/") + "/")
                             for o in ok)
    verb = "sync" if allowed else "copy"
    try:
        r = subprocess.run([_rclone(), verb, str(local), f"{remote()}{subpath}",
                            "--fast-list", "--checkers", "8", "--stats-one-line"],
                           capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        # An uncaught timeout killed the whole cycle before it could log anything
        raise UnavailableError(f"올리기 시간 초과 ({subpath}, {timeout}s) — 다음 회차에 이어서")
    if r.returncode != 0:
        raise UnavailableError(f"올리기 실패 ({subpath}): {r.stderr.strip()[:200]}")
    if mirror and not allowed:
        # 조용히 다르게 동작하면 다음 사람이 「미러인데 왜 안 지워지지」로 하루를 쓴다
        print(f"  [u_api] {subpath}: mirror 요청이지만 u.mirror_ok 에 없어 copy 로 올렸다")
    return sum(1 for ln in r.stderr.splitlines() if "Transferred:" in ln)
