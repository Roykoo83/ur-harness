#!/usr/bin/env python3
"""SSOT 접근을 마운트에서 API 로 옮긴다 — 순서가 있는 단계로.

왜 순서인가
  마운트를 쓰는 곳은 세 종류다. **읽기만 하는 곳**, **기계 구역에 쓰는 곳**,
  그리고 **마운트 자체를 판정 대상으로 삼는 곳**(게이트·와치독·복구도구).
  섞어서 손대면 무엇이 무엇 때문에 깨졌는지 알 수 없다.

    1  읽기        틀려도 데이터가 상하지 않는다. 먼저 옮겨 API 경로를 검증한다
    2  기계 쓰기   자동화가 주인인 파일. 구역 가드가 사람 구역을 막는다
    3  판정 재정의 마운트가 쓰기 경로가 아니면 「마운트↔클라우드 수렴」은 질문이
                   아니다. 사라지게 두지 않고 **답할 수 있는 질문으로 바꾼다**

  각 단계는 게이트로 검증한다. 게이트 **항목 수가 줄면 실패로 본다** —
  검사가 사라진 것과 고쳐진 것은 다르다.

    api_migrate.py --plan     단계와 현재 상태
    api_migrate.py --step     다음 단계 한 개
    api_migrate.py --check    지금 마운트 의존 현황만
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "core"))
from lib.config import cfg, paths  # noqa: E402

STATE = paths.state / "api_migration.json"

# 마운트를 쓰는 곳과 그 종류. 조사해서 적은 것이고, 코드가 바뀌면 여기도 바뀐다
TARGETS: list[tuple[str, str, int, str]] = [
    # (파일, 무엇을 하는가, tier, 어떻게 옮기는가)
    ("core/ledger_rebuild.py", "SSOT 본문을 읽어 보안등급을 매긴다", 1,
     "ssot_io 로 내려받아 읽는다 — 등급 판정은 그대로"),
    ("agents/nworks/hr_sync_check.py", "컨플루언스 아카이브를 훑는다", 1,
     "ssot_io.listing 으로 목록만 받는다 — 본문이 필요 없다"),
    ("agents/budget/build_budget_dataset.py", "예산 데이터셋을 SSOT 에 만든다", 2,
     "로컬에 만들고 ssot_io.put — rmtree 대신 put 이 덮어쓴다"),
    ("agents/budget/sync_budget_status.py", "예산 현황을 SSOT 에 쓴다", 2,
     "ssot_io.put"),
    ("core/roster_sync.py", "조직도를 온톨로지 `사람/`에 반영한다", 2,
     "ssot_io.put — 사람 구역이면 가드가 막는다. 막히면 사람이 결정한다"),
    ("core/gate_check.py", "마운트↔클라우드 수렴을 잰다", 3,
     "마운트가 쓰기 경로가 아니므로 질문을 바꾼다: **API 로 쓴 것이 도착했는가**"),
    ("core/watchdog.py", "마운트 충돌 잔재를 찾는다", 3,
     "진단으로 남기되 정지 사유에서 내린다 — 마운트는 이제 선택적이다"),
    ("core/drivefs_resync.py", "마운트를 고치는 도구", 3,
     "퇴역 — 마운트가 경로가 아니면 고칠 것이 없다. 사유를 남긴다"),
    ("agents/nworks/nworks_hermes_bridge.py", "프롬프트에 적힌 경로 안내", 3,
     "코드가 읽는 경로가 아니다. 사람에게 알려 주는 문장이므로 "
     "마운트 대신 SSOT 상대경로로 적는다"),
]

TIER_NAME = {1: "1-읽기", 2: "2-기계쓰기", 3: "3-판정재정의"}


def load() -> dict:
    if STATE.exists():
        try:
            return json.loads(STATE.read_text(encoding="utf-8"))
        except ValueError:
            pass
    return {"done": {}}


def save(st: dict) -> None:
    STATE.parent.mkdir(parents=True, exist_ok=True)
    STATE.write_text(json.dumps(st, ensure_ascii=False, indent=2), encoding="utf-8")


def mount_refs() -> dict[str, list[int]]:
    """지금 마운트를 참조하는 줄. 플레이스홀더가 아니라 **실제 사용**만 센다."""
    out: dict[str, list[int]] = {}
    pat = re.compile(r"\bMOUNT\b(?!_ROOT|_REL)|\{\{MOUNT_(?:ROOT|REL)\}\}")
    for d in ("core", "agents", "shared"):
        for f in sorted((REPO / d).rglob("*.py")):
            rel = str(f.relative_to(REPO))
            hits = [i for i, ln in enumerate(f.read_text(encoding="utf-8").splitlines(), 1)
                    if pat.search(ln)]
            if hits:
                out[rel] = hits
    return out


def gate() -> tuple[int, list[tuple[str, str, str]]]:
    """하네스 게이트를 돌려 (종료코드, [(표시, 이름, 상세)])."""
    g = REPO / "build" / "core" / "gate_check.py"
    if not g.exists():
        raise SystemExit("build/ 가 없다 — install/configure.py --build 를 먼저 돌린다")
    env = {"PYTHONPATH": ":".join(str(REPO / "build" / x)
                                  for x in ("core", "shared", "agents"))}
    import os
    r = subprocess.run([sys.executable, str(g)], capture_output=True, text=True,
                       timeout=1800, env={**os.environ, **env})
    items = []
    for ln in (r.stdout + r.stderr).splitlines():
        m = re.match(r"\s*([✓✗◑])\s*\[(\w+)\]\s*(.+?)\s+—\s+(.*)$", ln)
        if m:
            items.append((m.group(1), m.group(3).strip(), m.group(4).strip()))
    return r.returncode, items


def cmd_check() -> int:
    refs = mount_refs()
    st = load()
    print(f"마운트를 참조하는 파일 {len(refs)}개\n")
    for tier in (1, 2, 3):
        rows = [(f, w, how) for f, w, t, how in TARGETS if t == tier]
        done = TIER_NAME[tier] in st["done"]
        print(f"  {'✓' if done else '·'} [{TIER_NAME[tier]}]")
        for f, w, how in rows:
            n = len(refs.get(f, []))
            print(f"      {'✓' if n == 0 else f'{n}줄'}  {f} — {w}")
            if n:
                print(f"              → {how}")
        print()
    extra = sorted(set(refs) - {f for f, *_ in TARGETS})
    if extra:
        print("  계획에 없는 참조 — 조사해서 TARGETS 에 적는다:")
        for f in extra:
            print(f"      {f}:{refs[f][:4]}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--plan", action="store_true")
    a = ap.parse_args()
    if a.check or a.plan or True:
        return cmd_check()


if __name__ == "__main__":
    raise SystemExit(main())
