#!/usr/bin/env python3
"""추출 → 병합 → 생성을 한 번에. 시간당 사이클이 부를 진입점이다.

    python3 scripts/tasks/run.py
"""
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
STEPS = ["extract.py", "extract_observed.py", "questions.py",
         "ledger.py", "weekly_draft.py", "build.py", "inject.py"]


def main() -> int:
    for s in STEPS:
        r = subprocess.run([sys.executable, str(HERE / s)])
        if r.returncode != 0:
            # 한 단계가 실패하면 멈춘다. 오래된 원장으로 화면을 새로 그리면
            # **갱신된 것처럼 보이는 낡은 보드**가 되어 아무 경고도 남지 않는다.
            print(f"!! {s} 실패 — 중단 (기존 화면 유지)")
            return r.returncode
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
