#!/usr/bin/env python3
"""원장 회차 — 아침 08 · 점심 13 · 저녁 17.

U 를 먼저 훑고 원장을 만든다. **순서가 중요하다** — U 갱신 없이 원장을 만들면
오늘 만든 과제 파일이 원장에 안 들어간다.
실패해도 다음 단계를 막지 않는다. 원장이 한 스트림 때문에 통째로 멈추면
화면이 더 낡아진다.

왜 셸이 아니라 파이썬인가
  옛 `회차실행.sh` 는 `/usr/bin/stat -f%z` (BSD 전용) 을 썼다. 우분투에서는
  그 줄이 실패해 **도착 확인이 매번 거짓 불일치를 보고한다.** 윈도우에서는 아예 못 돈다.
  구현을 둘로 나누면 한쪽만 고쳐지고 다른 쪽이 조용히 낡는다 — 그래서 하나로 합쳤다.

    회차실행.py              전체 회차
    회차실행.py --skip-u     U 스캔 건너뛰고 원장만
    회차실행.py --dry-run    무엇을 할지만
"""
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
sys.path.insert(0, str(REPO / "core"))
from lib.config import cfg, paths  # noqa: E402

LOG_DIR = paths.logs / "원장"
LOG = LOG_DIR / "회차.log"
INV = paths.workspace / "재정비" / "인벤토리" / "최근"
OUT = paths.output / "원장"


def say(msg: str) -> None:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    line = f"[{datetime.now():%m-%d %H:%M:%S}] {msg}"
    with LOG.open("a", encoding="utf-8") as f:
        f.write(line + "\n")
    print(line)


def rclone() -> str | None:
    """rclone 실행 파일. **찾지 못하면 None 을 돌려준다 — '통과'로 치지 않는다.**

    2026-09 에 `shutil.which` 실패를 조용히 통과로 처리했다가 가드가 영구히 꺼졌다.
    작동 불가와 통과는 다른 것이다.
    """
    p = shutil.which("rclone")
    if p:
        return p
    for cand in ("/opt/homebrew/bin/rclone", "/usr/local/bin/rclone", "/usr/bin/rclone",
                 r"C:\Program Files\rclone\rclone.exe"):
        if Path(cand).is_file():
            return cand
    return None


def run(label: str, cmd: list[str], fatal: bool = False) -> bool:
    """한 단계. 실패를 기록하되 다음 단계를 막지 않는다 (fatal 이 아니면)."""
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=1800)
    except Exception as e:                                   # noqa: BLE001
        say(f"  !! {label} 실행 불가: {type(e).__name__}: {e}")
        return False
    if r.returncode != 0:
        tail = (r.stderr or r.stdout or "").strip().splitlines()
        say(f"  {'!!' if fatal else '  '} {label} 실패({r.returncode}): "
            f"{tail[-1][:120] if tail else ''}")
        return False
    return True


# ── U 스캔 ──────────────────────────────────────────────────────────────────
def scan_source(age: str, dry: bool) -> None:
    """원천(U) 최근 변경 갱신. **읽기 전용 — 어떤 파일도 만들지 않는다.**

    전수 인벤토리는 스냅샷으로 두고 최근 창만 다시 훑는다.
    전수를 매번 돌면 느리고, 스냅샷만 쓰면 오늘 만든 파일이 영영 안 보인다.
    """
    rc = rclone()
    if not rc:
        say("!! rclone 이 없다 — U 스캔을 건너뛴다 (직전 인벤토리로 진행)")
        return
    try:
        d = json.loads((HERE / "과제정의.json").read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        say(f"!! 과제정의.json 을 읽지 못했다: {e}")
        return
    drive_id = cfg("org.drives.source.id")
    remote = cfg("org.drives.source.remote", "gdrive")
    targets = [(p["key"], p["u"]) for p in d.get("과제", []) if p.get("u")]
    say(f"===== 원천 최근 {age} 갱신 시작 · 대상 {len(targets)}개 =====")
    INV.mkdir(parents=True, exist_ok=True)
    ok = fail = 0
    for key, path in targets:
        dest = INV / f"{key}.tsv"
        cmd = [rc, "lsf"]
        if drive_id:
            cmd += ["--drive-team-drive", str(drive_id)]
        cmd += [f"{remote}:{path}", "-R", "--files-only", "--max-age", age,
                "--format", "tsp", "--separator", "\t"]
        if dry:
            say(f"  (예정) {key}")
            continue
        try:
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
        except Exception as e:                               # noqa: BLE001
            say(f"  !! {key} 조회 불가: {e}"); fail += 1; continue
        if r.returncode == 0:
            dest.write_text(r.stdout, encoding="utf-8")
            say(f"  {key}: {len(r.stdout.splitlines())}건"); ok += 1
        else:
            # 직전 결과를 **지우지 않는다.** 실패했다고 빈 파일로 덮으면
            # 그 다음 단계가 "0건"을 사실로 읽는다.
            say(f"  !! {key} 조회 실패 (직전 결과 유지)"); fail += 1
        time.sleep(3)                                        # API 예의
    say(f"===== 원천 갱신 완료 · 성공 {ok} · 실패 {fail} =====")


# ── 배포 ────────────────────────────────────────────────────────────────────
def publish(dry: bool) -> None:
    """대시보드가 읽는 자리에 올린다.

    **마운트로 쓰지 않고 rclone 으로 올린다.** 마운트는 OS 별로 자동 실행 환경에서
    접근이 막히는 경우가 있다(macOS 샌드박스는 파일 단위 허가를 요구해, 자동 실행이
    처음 보는 파일에서 조용히 실패했다 — 2026-09 에 열여덟 회차를 그렇게 잃었다).
    rclone 은 네트워크로 드라이브 API 를 부르므로 이 문제가 없다.
    **이 폴더의 쓰기 경로는 rclone 하나뿐이다. 마운트 복사와 섞지 않는다.**
    """
    rc = rclone()
    if not rc:
        say("!! rclone 이 없다 — 배포하지 않는다 (로컬 원장은 최신)")
        return
    dept = cfg("ssot.dept_root", "")
    remote = cfg("org.drives.ssot.remote_rw", "SSOT_rw")
    dst = f"{remote}:{dept}/50_대시보드/10_운영OS/_데이터".replace("//", "/")
    srcs = [OUT / "최신.js", OUT / "최신.md", HERE / "화면" / "연동.js"]
    ok = True
    for src in srcs:
        if not src.is_file():
            say(f"  배포 원본 없음: {src.name}"); ok = False; continue
        if dry:
            say(f"  (예정) {src.name} → {dst}"); continue
        # 파이프를 쓰지 않는다. 셸판에서 `| grep -v NOTICE` 를 붙였더니
        # rclone 이 성공해도 grep 의 종료코드(1)가 실패로 읽혔다.
        r = subprocess.run([rc, "copyto", str(src), f"{dst}/{src.name}"],
                           capture_output=True, text=True, timeout=600)
        if r.returncode != 0:
            msg = [l for l in (r.stderr or "").splitlines() if "NOTICE" not in l][:2]
            say(f"  {src.name} 업로드 실패({r.returncode}): {' '.join(msg)[:140]}")
            ok = False
    if dry or not ok:
        if not ok:
            say("배포 실패 (로컬 원장은 최신)")
        return
    # **올렸다고 믿지 않고 서버에 물어 확인한다.**
    # 도착은 표식이 아니라 대상에게 묻는 것이다.
    want = (OUT / "최신.js").stat().st_size
    got = None
    r = subprocess.run([rc, "size", "--json", f"{dst}/최신.js"],
                       capture_output=True, text=True, timeout=120)
    if r.returncode == 0:
        try:
            got = json.loads(r.stdout).get("bytes")
        except ValueError:
            pass
    if want == got:
        say(f"SSOT 데이터 자리 갱신 ({got}B 도착 확인)")
    else:
        say(f"업로드는 됐으나 대조 불일치: 원본 {want}B / 서버 {got if got is not None else '없음'}B")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--age", default="21d", help="원천에서 다시 훑을 창 (기본 21d)")
    ap.add_argument("--skip-u", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()

    say("===== 회차 시작 =====")
    if not a.skip_u:
        scan_source(a.age, a.dry_run)
    py = sys.executable
    for label, script, fatal in (
        ("원장 생성",     "원장생성.py",     True),
        ("분류 요청 생성", "분류요청생성.py",  False),
        ("온톨로지 수확",  "온톨로지수확.py",  False),
    ):
        if a.dry_run:
            say(f"  (예정) {label}")
            continue
        run(label, [py, str(HERE / script)], fatal=fatal)
    publish(a.dry_run)
    say("===== 회차 종료 =====")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
