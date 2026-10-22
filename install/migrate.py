#!/usr/bin/env python3
"""스케줄러 이관 — 손으로 등록한 서비스를 정의 기반 생성본으로 한 개씩 옮긴다.

왜 한 개씩인가
  20개를 한 번에 바꾸면 무엇이 깨졌는지 알 수 없다. 그리고 돌고 있는 자동화는
  사람이 작업하는 동안에도 계속 돈다 — 2026-10-21 에 작업 중 끼어든 자동 승격이
  메일 54건을 잘못된 이름으로 올렸다.

  그래서 한 걸음이 **생성 → 옛것 정지 → 새것 등록 → 검증 → 기록**으로 닫힌다.
  검증에 실패하면 그 자리에서 되돌린다. 다음 걸음은 사람이 결정한다.

    migrate.py --plan             계획을 본다 (아무것도 바꾸지 않는다)
    migrate.py --step             다음 한 개를 옮긴다
    migrate.py --step --dry-run   무엇을 할지만 본다
    migrate.py --status           어디까지 왔는가
    migrate.py --rollback <이름>   그 한 개를 되돌린다
    migrate.py --verify <이름>     그 서비스가 실제로 돌았는지 확인만

재실행해도 안전하다. 이미 끝난 것은 건너뛴다.
"""
from __future__ import annotations

import argparse
import json
import os
import plistlib
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "core"))
from lib.config import cfg, paths  # noqa: E402

LA = Path.home() / "Library" / "LaunchAgents"
GEN = REPO / "install" / "launchd"
STATE = paths.state / "migration.json"

# ── 이관 순서 — 위험이 낮은 것부터 ──────────────────────────────────────────
#   tier 1  읽기 전용·되돌리기 쉬움
#   tier 2  쓰기는 하지만 정본 밖
#   tier 3  정본에 쓴다 — 가장 나중에, 가장 조심해서
#
# 옛 이름은 **파일명으로 추측하지 않는다.** 같은 일인데 구현이 바뀐 것들이 있다
# (회차실행.sh→.py · watchdog_cycle.sh→watchdog.py · rotate-logs.sh→rotate_logs.py).
PLAN = [
    # (새 서비스,          옛 label,                  tier, 메모)
    ("log-rotate",        "com.urban.log-rotate",      1, "로그 정리. 정본을 건드리지 않는다"),
    ("gate-check",        None,                        1, "신규 — 옛 등록 없음"),
    ("hr-sync-check",     "com.urban.hr-sync-check",   1, "조직도 대조. 읽기"),
    ("roster-sync",       None,                        1, "신규 — 옛 등록 없음"),
    ("tunnel-guard",      "com.urban.nworks-tunnel-guard", 2, "터널 감시"),
    ("source-changefeed", "com.urban.u-changefeed",    2, "원천 변경 추적"),
    ("watchdog",          "com.urban.watchdog",        2, "감시. 옛것은 셸 래퍼였다"),
    ("fathom-minutes",    "com.urban.fathom-minutes",  2, "회의 전사 수집"),
    ("mount-resync",      "com.urban.drivefs-resync",  2, "마운트 재동기화"),
    ("daily-brief",       "com.urban.nworks-brief",    2, "부서방 브리핑 — 외부 발신"),
    ("weekly-dashboard",  "com.urban.weekly-dashboard", 2, "대시보드 생성·게시"),
    ("budget-sync",       None,                        2, "신규 — 옛 등록 없음"),
    ("agent-bridge",      "com.urban.nworks-bridge",   2, "메신저 게이트웨이 — 상시"),
    ("ops-ledger",        "com.urban.ops-ledger",      3, "원장. 정본에 쓴다"),
    ("nworks-cycle",      "com.urban.nworks-mail",     3, "수집→판정. 정본 경로의 시작"),
    ("ssot-index",        "com.urban.ssot-index",      3, "색인 재생성. 정본에 쓴다"),
    ("ssot-promote",      "com.urban.ssot-promote",    3, "정본 승격 — **가장 나중에**"),
]
# 하네스 범위 밖 — 옮기지 않는다. 사람이 따로 판단한다
OUT_OF_SCOPE = {
    "com.urban.ctx-guard":     "옛 AX_Infra 프로젝트 도구",
    "com.urban.roy-index":     "옛 AX_Infra 프로젝트 도구",
    "com.urban.rpb-daily":     "옛 AX_Infra 프로젝트 도구",
    "com.urban.rpbd-tasks":    "옛 AX_Infra 프로젝트 도구",
    "com.urban.nworks-tunnel": "cloudflared 자체 — 하네스는 감시만 한다",
}


def sh(*cmd, timeout=60) -> tuple[int, str]:
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return r.returncode, (r.stdout + r.stderr).strip()
    except Exception as e:                                   # noqa: BLE001
        return 99, f"{type(e).__name__}: {e}"


def uid() -> int:
    return os.getuid()


def label_for(name: str) -> str:
    org = (cfg("org.domain") or "local").split(".")[0]
    return f"{org}.ur-{name}"


def loaded(label: str) -> bool:
    rc, out = sh("launchctl", "list")
    return any(line.endswith("\t" + label) or line.split("\t")[-1] == label
               for line in out.splitlines())


def load_state() -> dict:
    try:
        return json.loads(STATE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"done": {}, "failed": {}}


def save_state(s: dict) -> None:
    STATE.parent.mkdir(parents=True, exist_ok=True)
    STATE.write_text(json.dumps(s, ensure_ascii=False, indent=1), encoding="utf-8")


# ── 검증 ───────────────────────────────────────────────────────────────────
def is_keepalive(name: str) -> bool:
    try:
        import yaml                                          # noqa: PLC0415
        defs = yaml.safe_load((REPO / "install" / "services.yaml").read_text(encoding="utf-8"))
        for s in defs.get("services", []):
            if s["name"] == name:
                return bool((s.get("schedule") or {}).get("keepalive"))
    except Exception:                                        # noqa: BLE001
        pass
    return False


def running_pid(label: str) -> int | None:
    """상시 서비스가 지금 떠 있는가. PID 가 있으면 돌고 있는 것이다."""
    rc, out = sh("launchctl", "list")
    for line in out.splitlines():
        parts = line.split("\t")
        if len(parts) >= 3 and parts[-1] == label:
            return int(parts[0]) if parts[0].lstrip("-").isdigit() and parts[0] != "-" else None
    return None


def verify(name: str, *, wait: int = 90) -> tuple[bool, str]:
    """그 서비스가 **실제로 돌았는지** 본다. 등록됐다는 것만으로는 통과가 아니다.

    등록 → 강제 1회 실행(kickstart) → 로그가 자라는지 확인.
    로그가 안 자라면 「확인 불가」이고, 그것은 「통과」가 아니다.
    """
    label = label_for(name)
    if not loaded(label):
        return False, "등록되지 않았다"
    log = paths.logs / f"ur-{name}.log"
    before = log.stat().st_size if log.is_file() else -1

    rc, out = sh("launchctl", "kickstart", "-k", f"gui/{uid()}/{label}")
    if rc != 0:
        return False, f"kickstart 실패({rc}): {out[:80]}"

    # 상시 서비스는 **떠 있는지**로 본다. kickstart -k 는 기존 프로세스를 죽이므로
    # 그때의 종료코드 15(SIGTERM)는 실패가 아니다 — 그걸 실패로 읽으면
    # 정상인 데몬을 매번 되돌리게 된다
    if is_keepalive(name):
        for _ in range(10):
            time.sleep(3)
            if (pid := running_pid(label)):
                time.sleep(5)                                 # 바로 죽지 않는지 본다
                if running_pid(label):
                    return True, f"상시 서비스가 떠 있다 (pid {pid})"
        return False, "상시 서비스가 뜨지 않았다"

    deadline = time.time() + wait
    grew = 0
    while time.time() < deadline:
        time.sleep(3)
        if log.is_file() and log.stat().st_size > max(before, 0):
            grew = log.stat().st_size - max(before, 0)
            # **이번 실행이 쓴 부분만 본다.** 꼬리를 읽으면 직전 실패의 찌꺼기를
            # 이번 오류로 오독한다 (실측: 되돌린 뒤 재시도가 옛 로그 때문에 또 실패했다)
            with log.open("rb") as fh:
                fh.seek(max(before, 0))
                fresh = fh.read().decode("utf-8", errors="replace")
            bad = [w for w in ("Traceback", "ModuleNotFoundError", "No such file",
                               "command not found", "Permission denied") if w in fresh]
            if bad:
                last = [l for l in fresh.strip().splitlines() if l.strip()]
                return False, f"로그에 오류: {bad[0]} — {last[-1][:90] if last else ''}"
            break
        # 로그가 비어 있어도 정상 종료했을 수 있다 — launchd 에 직접 묻는다.
        # **빈 로그를 「증가」로 치면 안 된다.** 돌지 않은 것과 구분이 안 된다
        code = last_exit(label)
        if code is not None:
            if code == 0:
                return True, "launchd 종료코드 0 (로그 출력 없음)"
            return False, f"launchd 종료코드 {code}"
    if grew:
        return True, f"로그 {grew}바이트 증가"
    return False, f"{wait}초 동안 아무 흔적이 없다 — 돌았는지 **확인 불가**"


def last_exit(label: str) -> int | None:
    """launchd 가 기록한 마지막 종료코드. 아직 안 돌았으면 None.

    로그가 비어 있어도 돌았는지 알 수 있는 유일한 신호다.
    """
    rc, out = sh("launchctl", "list", label)
    if rc != 0:
        return None
    m = re.search(r'"LastExitStatus"\s*=\s*(-?\d+)', out)
    return int(m.group(1)) if m else None


def stop_old(old_label: str, dry: bool) -> str:
    """옛 서비스를 정지하고 plist 를 보관한다. **지우지 않는다** — 되돌릴 수 있어야 한다."""
    p = LA / f"{old_label}.plist"
    if not p.is_file():
        return "옛 plist 없음"
    if dry:
        return f"(예정) bootout + {p.name} → .migrated"
    sh("launchctl", "bootout", f"gui/{uid()}/{old_label}")
    keep = p.with_suffix(".plist.migrated-" + time.strftime("%Y%m%d"))
    shutil.move(str(p), str(keep))
    return f"정지 · 보관 {keep.name}"


def start_new(name: str, dry: bool) -> tuple[bool, str]:
    label = label_for(name)
    src = GEN / f"{label}.plist"
    if not src.is_file():
        return False, f"생성본 없음: {src.name} — configure.py --launchd 를 먼저 돌린다"
    if dry:
        return True, f"(예정) {src.name} → LaunchAgents + bootstrap"
    dst = LA / src.name
    shutil.copy2(src, dst)
    sh("launchctl", "bootout", f"gui/{uid()}/{label}")          # 혹시 떠 있으면 내린다
    rc, out = sh("launchctl", "bootstrap", f"gui/{uid()}", str(dst))
    if rc != 0 and not loaded(label):
        return False, f"bootstrap 실패({rc}): {out[:90]}"
    return True, "등록"


def rollback(name: str, old_label: str | None) -> str:
    """새것을 내리고 옛것을 되살린다."""
    label = label_for(name)
    sh("launchctl", "bootout", f"gui/{uid()}/{label}")
    (LA / f"{label}.plist").unlink(missing_ok=True)
    if not old_label:
        return "새것만 내림 (옛 등록이 없던 서비스)"
    kept = sorted(LA.glob(f"{old_label}.plist.migrated-*"))
    if not kept:
        return "새것 내림 · 되살릴 옛 plist 없음"
    back = LA / f"{old_label}.plist"
    shutil.move(str(kept[-1]), str(back))
    sh("launchctl", "bootstrap", f"gui/{uid()}", str(back))
    return f"새것 내림 · 옛것 복구({old_label})"


# ── 루프 ───────────────────────────────────────────────────────────────────
def next_item(st: dict):
    blocked = st.get("blocked") or {}
    for name, old, tier, memo in PLAN:
        if name in st["done"] or name in blocked:
            continue
        return name, old, tier, memo
    return None


def cmd_plan() -> int:
    st = load_state()
    print(f"이관 계획 — {len(PLAN)}개 · 끝남 {len(st['done'])} · 남음 {len(PLAN)-len(st['done'])}\n")
    cur = next_item(st)
    for tier in (1, 2, 3):
        rows = [p for p in PLAN if p[2] == tier]
        head = {1: "읽기 전용·되돌리기 쉬움", 2: "쓰기 있음 · 정본 밖",
                3: "정본에 쓴다 — 가장 조심"}[tier]
        print(f"  [tier {tier}] {head}")
        for name, old, _t, memo in rows:
            mark = "✓" if name in st["done"] else ("▶" if cur and cur[0] == name else "·")
            print(f"    {mark} {name:<18} {'← ' + old.replace('com.urban.','') if old else '(신규)':<22} {memo}")
        print()
    if OUT_OF_SCOPE:
        print("  [범위 밖] 옮기지 않는다 — 사람이 따로 판단한다")
        for k, v in OUT_OF_SCOPE.items():
            print(f"    · {k.replace('com.urban.',''):<20} {v}")
    print(f"\n  다음: {cur[0] if cur else '없음 — 전부 끝났다'}")
    print("  한 걸음 = 생성 → 옛것 정지 → 새것 등록 → **검증** → 기록")
    print("  검증 실패하면 그 자리에서 되돌린다. migrate.py --step")
    return 0


def cmd_status() -> int:
    st = load_state()
    bl = st.get("blocked", {})
    print(f"끝남 {len(st['done'])}/{len(PLAN)} · 실패 {len(st.get('failed', {}))} · 막힘 {len(bl)}")
    for name, info in st["done"].items():
        print(f"  ✓ {name:<18} {info.get('at','')[:16]}  {info.get('note','')}")
    for name, info in (st.get("failed") or {}).items():
        print(f"  ✗ {name:<18} {info.get('at','')[:16]}  {info.get('why','')}")
    for name, info in (st.get("blocked") or {}).items():
        print(f"  ■ {name:<18} 막힘 — {info.get('why','')[:70]}")
        if info.get("선행조건"):
            print(f"       선행: {info['선행조건']}")
    cur = next_item(st)
    print(f"\n  다음: {cur[0] if cur else '없음'}")
    return 0


def cmd_step(dry: bool, force: str | None) -> int:
    st = load_state()
    item = next((p for p in PLAN if p[0] == force), None) if force else next_item(st)
    if not item:
        print("전부 끝났다.")
        return 0
    name, old, tier, memo = item
    print(f"▶ {name}  (tier {tier}) — {memo}")
    if old:
        print(f"  옛 서비스: {old}")

    # 0) 생성본이 최신인지
    rc, out = sh(sys.executable, str(REPO / "install" / "configure.py"),
                 "--launchd", "--with-optional", timeout=120)
    if rc not in (0, 1):
        print(f"  ✗ 생성 실패: {out[-200:]}")
        return 2

    # 1) 옛것 정지
    print(f"  1. 옛것 정지 … {stop_old(old, dry) if old else '옛 등록 없음'}")

    # 2) 새것 등록
    ok, msg = start_new(name, dry)
    print(f"  2. 새것 등록 … {msg}")
    if not ok:
        if not dry and old:
            print(f"  ↩ 되돌림: {rollback(name, old)}")
        return 2
    if dry:
        print("  3. 검증 … (dry-run 이라 건너뜀)")
        print("\n  [dry-run] 실제로는 바꾸지 않았다")
        return 0

    # 3) 검증 — 등록이 아니라 **실행**을 본다
    ok, why = verify(name)
    print(f"  3. 검증 … {'OK' if ok else '실패'} — {why}")
    if not ok:
        print(f"  ↩ 되돌림: {rollback(name, old)}")
        st.setdefault("failed", {})[name] = {"at": time.strftime("%Y-%m-%dT%H:%M:%S"), "why": why}
        save_state(st)
        print("\n  멈춘다. 원인을 고친 뒤 다시 --step 한다")
        return 1

    # 4) 기록
    st["done"][name] = {"at": time.strftime("%Y-%m-%dT%H:%M:%S"), "old": old, "note": why}
    st.get("failed", {}).pop(name, None)
    save_state(st)
    left = len(PLAN) - len(st["done"])
    print(f"\n  ✓ {name} 완료 · 남은 {left}개" + ("" if left else " — 전부 끝났다"))
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--plan", action="store_true")
    ap.add_argument("--status", action="store_true")
    ap.add_argument("--step", action="store_true")
    ap.add_argument("--only", help="특정 서비스 하나만 (순서를 건너뛴다)")
    ap.add_argument("--rollback", metavar="이름")
    ap.add_argument("--verify", metavar="이름")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()

    if sys.platform != "darwin" and (a.step or a.rollback or a.verify):
        print("이 도구는 macOS launchd 용이다. Linux·Windows 는 "
              "configure.py --units / --tasks 로 바로 만든다", file=sys.stderr)
        return 2

    if a.plan or not any((a.status, a.step, a.rollback, a.verify)):
        return cmd_plan()
    if a.status:
        return cmd_status()
    if a.verify:
        ok, why = verify(a.verify)
        print(f"{a.verify}: {'OK' if ok else '실패'} — {why}")
        return 0 if ok else 1
    if a.rollback:
        old = next((o for n, o, _t, _m in PLAN if n == a.rollback), None)
        print(rollback(a.rollback, old))
        st = load_state(); st["done"].pop(a.rollback, None); save_state(st)
        return 0
    return cmd_step(a.dry_run, a.only)


if __name__ == "__main__":
    raise SystemExit(main())
