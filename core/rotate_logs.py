#!/usr/bin/env python3
"""로그 정리 — 서비스 로그가 디스크를 채우지 않게 한다.

systemd 유닛은 `append:` 로 로그를 무한히 덧붙인다. 아무도 지우지 않으면
몇 달 뒤 디스크가 찬다. 그때는 모든 서비스가 동시에 죽는다.

  rotate_logs.py            기본값으로 정리 (16MB 초과분을 돌리고 14일 지난 것을 지운다)
  rotate_logs.py --dry-run  무엇을 할지만 보여 준다
"""
from __future__ import annotations

import argparse
import gzip
import shutil
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "core"))
try:
    from lib.config import paths
    LOGS = paths.logs
except Exception:                                      # noqa: BLE001
    LOGS = Path.home() / "ur-harness-work" / "logs"

MAX_BYTES = 16 * 1024 * 1024
KEEP_DAYS = 14


def rotate(log: Path, dry: bool, limit: int = MAX_BYTES) -> str | None:
    """한도를 넘은 로그를 .1.gz 로 돌리고 원본을 비운다."""
    try:
        size = log.stat().st_size
    except OSError:
        return None
    if size <= limit:
        return None
    dest = log.with_suffix(log.suffix + f".{time.strftime('%Y%m%d-%H%M%S')}.gz")
    if dry:
        return f"돌림 {log.name} ({size / 1e6:.1f}MB) → {dest.name}"
    with log.open("rb") as src, gzip.open(dest, "wb") as out:
        shutil.copyfileobj(src, out)
    # 자른다 — 지우지 않는다. 서비스가 열어 둔 핸들을 깨뜨리지 않기 위해서다
    with log.open("r+b") as f:
        f.truncate(0)
    return f"돌림 {log.name} ({size / 1e6:.1f}MB) → {dest.name}"


def prune(root: Path, days: int, dry: bool) -> list[str]:
    cutoff = time.time() - days * 86400
    out = []
    for p in sorted(root.glob("*.gz")):
        try:
            if p.stat().st_mtime < cutoff:
                out.append(f"지움 {p.name}")
                if not dry:
                    p.unlink()
        except OSError:
            continue
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dir", default=None, help="로그 폴더 (기본: 설정의 workspace/logs)")
    ap.add_argument("--max-mb", type=float, default=MAX_BYTES / 1e6)
    ap.add_argument("--keep-days", type=int, default=KEEP_DAYS)
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()

    root = Path(a.dir).expanduser() if a.dir else LOGS
    if not root.is_dir():
        print(f"로그 폴더가 없다: {root}  (아직 아무것도 돌지 않았다는 뜻일 수 있다)")
        return 0

    limit = int(a.max_mb * 1e6)
    done = [m for p in sorted(root.glob("*.log")) if (m := rotate(p, a.dry_run, limit))]
    done += prune(root, a.keep_days, a.dry_run)

    head = "할 일" if a.dry_run else "처리"
    print(f"{root} · {head} {len(done)}건")
    for d in done:
        print(f"  {d}")
    if not done:
        print("  정리할 것 없음")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
