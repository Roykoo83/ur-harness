#!/usr/bin/env python3
"""SSOT 쓰기·읽기 단일 창구 — 보완기획 §3-2 f

**업로드 성공(rc=0)은 도착을 증명하지 않는다.** 해시 대조까지 해야 "☁ 반영"이라 말할 수 있다.
그리고 원격을 `gdrive:`(rclone 공용 client_id, Roy OAuth)에서 `SSOT_rw:`(서비스계정)로 옮긴다 —
공용 client 는 분당 쿼터가 상시 포화라 403 이 반복되고 2026년 중 폐기 예고됐다.

  from lib.ssot_io import put, verify
  ok = put(local_path, "{{DEPT_ROOT}}/50_대시보드/10_운영OS")   # 업로드 + 해시검증
"""
from __future__ import annotations
import shutil, subprocess
from pathlib import Path

RCLONE = shutil.which("rclone") or "/opt/homebrew/bin/rclone"

# ── 쓰기 구역표 — M 안 (Roy 결정 2026-09-07) ───────────────────────────────
# 한 경로에 쓰는 주체는 하나다. 이 창구(API)로 쓸 수 있는 곳은 아래 **기계 구역**만이다.
# 나머지는 전부 사람(의 AI)이 마운트로 쓰는 구역이고, 자동화가 API 로 거기 쓰면
# DriveFS 와 경합해 「이름 2」·동명 사본이 생긴다 (2026-09-07 주간보고 pptx · 대시보드 8쌍 실측).
# 표의 정본: SSOT/50_도구상자/00_규범/SSOT_쓰기구역표.md — 여기와 어긋나면 그쪽이 맞다.
MACHINE_ZONES = (
    "_시스템/",
    "{{DEPT_ROOT}}/40_커뮤니케이션/메일/",
    "{{DEPT_ROOT}}/40_커뮤니케이션/메신저/",
    "00_공통/20_일정/naverworks_캘린더/",
    "{{DEPT_ROOT}}/50_대시보드/",
    "{{DEPT_ROOT}}/00_부서정본/주간보고_초안/",
)
MACHINE_MARKERS = ("/_API스냅샷_", "/_INDEX.md", "/_동기화_확인.md")   # 경로 어디에 있어도 기계 소유


def in_machine_zone(rel: str) -> bool:
    import unicodedata
    r = unicodedata.normalize("NFC", rel).lstrip("/")
    return r.startswith(MACHINE_ZONES) or any(m in "/" + r for m in MACHINE_MARKERS)
RW = "SSOT_rw:"          # 서비스계정 · SSOT 콘텐츠관리자
RO = "SSOT_ro:"          # 서비스계정 · 읽기전용
LEGACY = "gdrive,team_drive={{SSOT_DRIVE_ID}}:"   # 폴백(쿼터 포화 가능)
TIMEOUT = 300


def _remotes() -> list[str]:
    """SSOT_rw(서비스계정) 우선. 없으면 구 원격으로 폴백하되 **눈에 보이게** 경고한다.

    codexx 교차검증 C 지적: 폴백이 조용히 남아 있으면 "쓰기 경로 전환 완료"가 거짓이 된다.
    구 원격(gdrive, rclone 공용 client_id)은 분당 쿼터 상시 포화 + 2026년 폐기 예고다.
    """
    try:
        out = subprocess.run([RCLONE, "listremotes"], capture_output=True,
                             text=True, timeout=30).stdout
        if "SSOT_rw:" in out:
            return [RW]
    except Exception:                                   # noqa: BLE001
        pass
    import sys as _sys
    print("  ! 경고: SSOT_rw 원격이 없어 구 원격(gdrive)으로 폴백한다 — "
          "쿼터 포화로 실패할 수 있다. scripts/drive_remotes_setup.sh 실행 필요",
          file=_sys.stderr)
    try:                                                # 폴백 사용을 하트비트로도 남긴다
        from pathlib import Path as _P
        _sys.path.insert(0, str(_P(__file__).resolve().parents[1]))
        from lib import hb as _hb                       # noqa: PLC0415
        _hb.fail("ssot-io-fallback", "SSOT_rw 원격 없음 — 구 원격 폴백 사용")
    except Exception:                                   # noqa: BLE001
        pass
    return [LEGACY]


def put(src: Path | str, rel_dst: str, verify_after: bool = True,
        quiet: bool = True, allow_human_zone: bool = False) -> tuple[bool, str]:
    """파일이면 copyto, 폴더면 copy. (성공여부, 메시지). verify_after 면 해시 대조까지.

    **사람 구역에는 쓰지 않는다.** 거기는 마운트로 쓴다. 정말 필요하면
    allow_human_zone=True 를 명시하고 그 사유를 호출부 주석에 남긴다.
    """
    src = Path(src)
    if not src.exists():
        return False, f"원본 없음: {src}"
    if not allow_human_zone and not in_machine_zone(rel_dst):
        return False, (f"구역 위반: `{rel_dst}` 는 사람 구역 — API 로 쓰지 않는다. "
                       f"마운트로 쓰거나 구역표(50_도구상자/00_규범/SSOT_쓰기구역표.md)를 고친다")
    remote = _remotes()[0]
    op = "copyto" if src.is_file() else "copy"
    dst = f"{remote}{rel_dst}"
    try:
        r = subprocess.run([RCLONE, op, str(src), dst, "--drive-upload-cutoff", "64M"]
                           + (["-q"] if quiet else []),
                           capture_output=True, text=True, timeout=TIMEOUT)
        if r.returncode != 0:
            return False, f"업로드 rc={r.returncode}: {(r.stderr or '')[-150:]}"
        if not verify_after:
            return True, "업로드 완료(미검증)"
        # 도착 검증 — 이것이 "☁ 반영" 의 근거다.
        # 파일은 `rclone check` 가 디렉토리를 요구하므로 원격 md5 를 직접 대조한다.
        if src.is_file():
            import hashlib
            h = hashlib.md5()                            # noqa: S324  (무결성 대조용)
            with src.open("rb") as fh:
                for blk in iter(lambda: fh.read(1 << 20), b""):
                    h.update(blk)
            m = subprocess.run([RCLONE, "md5sum", dst], capture_output=True,
                               text=True, timeout=TIMEOUT)
            if m.returncode != 0 or not m.stdout.strip():
                return False, f"원격 해시 조회 실패: {(m.stderr or '')[-120:]}"
            remote_md5 = m.stdout.split()[0].lower()
            if remote_md5 != h.hexdigest():
                return False, f"해시 불일치 (원격 {remote_md5[:12]}… ≠ 로컬 {h.hexdigest()[:12]}…)"
            return True, "업로드+해시검증 완료"
        c = subprocess.run([RCLONE, "check", str(src), dst, "--one-way", "--checksum"]
                           + (["-q"] if quiet else []),
                           capture_output=True, text=True, timeout=TIMEOUT)
        if c.returncode != 0:
            return False, f"검증 불일치 rc={c.returncode}: {(c.stderr or '')[-150:]}"
        return True, "업로드+해시검증 완료"
    except (OSError, subprocess.TimeoutExpired) as e:
        return False, f"{type(e).__name__}: {e}"


def sync_generated(src_dir: Path | str, rel_dst: str, max_delete: int = 30,
                   quiet: bool = True) -> tuple[bool, str]:
    """**전량 재생성되는 기계 구역 폴더**(대시보드 등)를 스테이지 → 클라우드로 동기화한다.

    put() 의 copy 는 클라우드에 남은 옛 파일을 지우지 않는다. 생성물 폴더는 원본이
    곧 전체이므로 sync 가 맞다. 순서: dedupe(동명 파일 정리) → sync → check.
    max_delete 는 폭주 방지 — 넘으면 rclone 이 아무것도 지우지 않고 rc≠0 으로 멈춘다.
    사람 구역에는 절대 쓰지 않는다 (allow 옵션 없음 — 생성물 폴더에만 쓰는 함수다).
    """
    src = Path(src_dir)
    if not src.is_dir():
        return False, f"스테이지 폴더 없음: {src}"
    if not in_machine_zone(rel_dst):
        return False, f"구역 위반: `{rel_dst}` 는 사람 구역 — sync 금지"
    remote = _remotes()[0]
    dst = f"{remote}{rel_dst}"
    q = ["-q"] if quiet else []
    try:
        # 1) 동명 파일이 있으면 sync 가 "duplicate object" 로 실패한다 — 먼저 정리
        subprocess.run([RCLONE, "dedupe", "newest", dst] + q,
                       capture_output=True, text=True, timeout=TIMEOUT)
        # 2) 반영 — **copy 다. sync 가 아니다.** 2026-09-08: sync 가 스테이지에 없는 파일을 지웠는데,
        #    그 폴더에 부서원이 올린 파일(10_운영OS/KSH/*, _ksh.html, _기획_*.md)이 있었다.
        #    "기계 구역이니 전부 기계 파일"이라는 가정이 틀렸다. 자동화는 자기가 만든 파일만
        #    덮어쓰고, 남의 파일은 절대 지우지 않는다. 낡은 생성물 정리가 필요하면 이름을 명시해 지운다.
        r = subprocess.run([RCLONE, "copy", str(src), dst, "--checksum", "--exclude", ".*",
                            "--drive-upload-cutoff", "64M"] + q,
                           capture_output=True, text=True, timeout=TIMEOUT)
        if r.returncode != 0:
            return False, f"sync rc={r.returncode}: {(r.stderr or '')[-160:]}"
        # 3) 도착 검증 (한 방향 — 스테이지의 것이 전부 도착했는가. 목적지에 다른 파일이 있어도 된다)
        c = subprocess.run([RCLONE, "check", str(src), dst, "--one-way", "--checksum",
                            "--exclude", ".*"] + q,
                           capture_output=True, text=True, timeout=TIMEOUT)
        if c.returncode != 0:
            return False, f"검증 불일치 rc={c.returncode}: {(c.stderr or '')[-160:]}"
        return True, "sync+해시검증 완료"
    except (OSError, subprocess.TimeoutExpired) as e:
        return False, f"{type(e).__name__}: {e}"


def listing(rel_path: str) -> list[str] | None:
    """SSOT 경로의 파일 목록(API 기준, 상대경로). 마운트를 믿지 않는다.

    count() 로 건수만 보면 "몇 개인가"는 알지만 "무엇이 있는가"는 모른다 —
    업무무관 메일이 섞였는지 같은 판정에는 목록이 필요하다.
    """
    try:
        r = subprocess.run([RCLONE, "lsf", "-R", "--files-only", f"{RO}{rel_path}"],
                           capture_output=True, text=True, timeout=300)
        if r.returncode != 0:
            return None
        return [l for l in r.stdout.splitlines() if l.strip()]
    except Exception:                                   # noqa: BLE001
        return None


def count(rel_path: str) -> int | None:
    """SSOT 경로의 파일 수(API 기준). 마운트를 믿지 않는다."""
    try:
        r = subprocess.run([RCLONE, "lsf", "-R", "--files-only", f"{RO}{rel_path}"],
                           capture_output=True, text=True, timeout=120)
        if r.returncode != 0:
            return None
        return len([l for l in r.stdout.splitlines() if l.strip()])
    except Exception:                                   # noqa: BLE001
        return None
