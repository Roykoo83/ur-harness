#!/usr/bin/env python3
"""정합성 복구 — 순서가 있는 단계로 하나씩 닫는다.

왜 순서인가
  게이트가 실패 10건을 보고한다. 그런데 그중 일부는 **내가 어제 바꾼 것의 여파**이고
  일부는 원래 있던 문제다. 섞어서 손대면 무엇이 무엇 때문인지 알 수 없다.
  그리고 여파를 먼저 치우지 않으면, 진짜 문제가 소음에 묻힌다.

  그래서 단계를 이렇게 둔다.

    1  여파 정리    접두·경계 정의가 세 곳에 흩어진 것을 한 곳으로
    2  데이터 경로   하네스가 실제 데이터를 보게 한다
    3  승격 이관     그래야 막아 둔 ssot-promote 를 옮길 수 있다
    4  상시 위반     원래 있던 문제 — 하나씩 사람이 판단

  각 단계는 **게이트로 검증**한다. 그 단계가 닫아야 할 항목이 실제로 닫혔는지
  확인하고, 닫히지 않으면 다음으로 가지 않는다.

    remediate.py --plan      단계와 현재 상태
    remediate.py --step      다음 단계 한 개
    remediate.py --check     지금 게이트 상태만
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

def live_root() -> Path:
    """지금 돌고 있는 운영 트리. 설정이 소유한다 — 조직 이름을 코드에 박지 않는다.

        legacy:
          root: "~/<운영 트리>"          # 이관 전 시스템이 있는 곳
          mail_output: "projects/.../output"
    """
    v = cfg("legacy.root")
    if not v:
        raise SystemExit(
            "설정에 legacy.root 가 없다.\n"
            "  이 도구는 **이관 전 시스템이 있는 환경**에서만 쓴다.\n"
            "  config/harness.yaml 에 legacy.root 를 적는다")
    return Path(v).expanduser()


GATE_REL = "scripts/gate_check.py"
STATE = paths.state / "remediation.json"


def _curate_dir() -> str:
    """운영본 mail_curate 가 있는 곳 — 접두·계열 경계의 소유자."""
    rel = cfg("legacy.mail_scripts")
    if not rel:
        raise SystemExit(
            "설정에 legacy.mail_scripts 가 없다 — 운영본 mail_curate.py 가 있는\n"
            "  legacy.root 기준 상대경로를 config/harness.yaml 에 적는다")
    return str(live_root() / rel)


def _frozen_series() -> str:
    """frozen 계열 키. 게이트 패치에 박지 않고 SERIES 에서 읽는다."""
    import importlib.util
    f = Path(_curate_dir()) / "mail_curate.py"
    spec = importlib.util.spec_from_file_location("mc", f)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    froz = [k for k, (st, _p) in m.SERIES.items() if st == "frozen"]
    return froz[-1] if froz else ""

# 단계 → 그 단계가 닫아야 할 게이트 항목(이름 일부로 맞춘다)
PHASES = [
    ("1-정의단일화", "접두·계열경계 정의를 한 곳으로 (mail_curate.SERIES)",
     ["접두 없는 메일 파일", "계열 경계"]),
    ("2-데이터경로", "하네스가 실제 데이터를 보게 한다 (operator.workspace)",
     []),                                        # 게이트 항목이 아니라 선행조건
    ("3-승격이관", "ssot-promote 를 정의 기반 서비스로",
     ["ssot-promote 성공 상태", "대장 승격분이 SSOT 에 도착"]),
    ("4-상시위반", "원래 있던 문제 — 사람이 판단할 것을 모아 보고",
     ["마운트 참조", "배포 보정기", "수렴", "health 보드", "업무무관 메일"]),
]


REPORT_ONLY = {"4-상시위반": True}
MEMO = "<!-- 조사 메모 — 여기부터는 다시 돌려도 보존된다 -->"


def gate() -> tuple[int, list[tuple[str, str, str]]]:
    """게이트를 돌려 (종료코드, [(표시, 이름, 상세)]) 를 돌려준다."""
    r = subprocess.run([sys.executable, str(live_root() / GATE_REL)], capture_output=True,
                       text=True, timeout=900)
    out = (r.stdout + r.stderr).splitlines()
    items = []
    for i, l in enumerate(out):
        s = l.strip()
        if s[:1] in ("✓", "✗", "◑"):
            detail = out[i + 1].strip() if i + 1 < len(out) else ""
            items.append((s[0], s[1:].strip(), detail))
    return r.returncode, items


def failing(items) -> list[tuple[str, str, str]]:
    return [x for x in items if x[0] in ("✗", "◑")]


def owned_by(phase_keys: list[str], items) -> list[tuple[str, str, str]]:
    return [x for x in failing(items)
            if any(k in x[1] for k in phase_keys)]


def load_state() -> dict:
    try:
        return json.loads(STATE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"done": {}}


def save_state(s: dict) -> None:
    STATE.parent.mkdir(parents=True, exist_ok=True)
    STATE.write_text(json.dumps(s, ensure_ascii=False, indent=1), encoding="utf-8")


# ── 단계 1 · 정의 단일화 ────────────────────────────────────────────────────
def phase1(dry: bool) -> tuple[bool, str]:
    """접두·계열 경계를 `mail_curate.SERIES` 한 곳에서만 읽게 한다.

    지금은 세 곳에 따로 적혀 있다 — curate · promote · gate.
    어긋나면 게이트가 정상인 것을 위반으로 보고한다(실제로 그랬다: `bot-` 를 몰라
    50건을 「접두 없는 파일」로 셌다). 소음이 반복되면 사람이 게이트를 안 읽는다.
    """
    g = live_root() / GATE_REL
    t = g.read_text(encoding="utf-8")
    changed = []

    # (1) 접두 — 정규식을 SERIES 에서 만든다
    old_re = r'not _re.search(r"/(?:rbp|roy)-\d+_", f)'
    if old_re in t:
        t = t.replace(old_re, 'not _re.search(_mail_prefix_re(), f)')
        changed.append("접두 정규식")

    # (2) 계열 경계 — 자체 T0 대신 SERIES_WINDOW
    old_t0 = '''        T0 = "2026-08-12T15:29:53+09:00"
        bad = [r for r in led
               if (r["series"] == "{frozen}") != (r["received"] < T0) and r["received"] != T0]
        froz = sum(1 for r in led if r["series"] == "{frozen}")'''
    new_t0 = '''        # 경계는 mail_curate.SERIES_WINDOW 가 소유한다 — 여기에 또 적지 않는다.
        # 계열이 늘 때(2026-10-21 봇 계열 추가) 하나의 T0 로 재면 거짓 위반이 난다
        _w = _series_windows()
        bad = []
        for r in led:
            lo, hi = _w.get(r["series"], (None, None))
            rt = r["received"]
            if rt and ((lo and rt < lo) or (hi and rt > hi)):
                bad.append(r)
        froz = sum(1 for r in led if r["series"] == "{frozen}")'''
    frozen = _frozen_series()
    o, n = old_t0.format(frozen=frozen), new_t0.format(frozen=frozen)
    if o in t:
        t = t.replace(o, n)
        changed.append("계열 경계")
    elif n.splitlines()[2] not in t:
        # 원문도 없고 새 블록도 없다 — 게이트가 예상과 다르다. 통과시키지 않는다
        return False, "게이트의 계열 경계 블록을 찾지 못했다 — 손으로 확인할 것"

    # (3) 헬퍼 주입
    helper = '''

def _mail_curate():
    """접두·경계의 소유자. 못 읽으면 멈춘다 — 추측하지 않는다."""
    import sys as _s
    _s.path.insert(0, "{curate}")
    import mail_curate                                        # noqa: PLC0415
    return mail_curate


def _mail_prefix_re() -> str:
    pres = [p.rstrip("-") for _st, p in _mail_curate().SERIES.values()]
    return r"/(?:" + "|".join(sorted(set(pres))) + r")-\\d+_"


def _series_windows() -> dict:
    return getattr(_mail_curate(), "SERIES_WINDOW", {{}})

'''.format(curate=_curate_dir())
    if changed and "_mail_prefix_re" not in t:
        i = t.index("\ndef ")
        t = t[:i] + helper + t[i:]

    if not changed:
        return True, "이미 단일화돼 있다"
    if dry:
        return True, f"(예정) {', '.join(changed)}"
    g.write_text(t, encoding="utf-8")
    import ast
    ast.parse(t)
    return True, f"고침: {', '.join(changed)}"


# ── 단계 2 · 데이터 경로 ────────────────────────────────────────────────────
def phase2(dry: bool) -> tuple[bool, str]:
    """하네스가 **실제 데이터**를 보게 한다.

    하네스 기본 작업공간은 비어 있고 운영 데이터는 옛 트리에 있다.
    데이터를 옮기지 않는다 — 돌고 있는 것을 움직이는 쪽이 훨씬 위험하다.
    대신 **설정이 실제 위치를 가리키게** 한다.
    """
    live_out = live_root() / cfg("legacy.mail_output", "")
    if not (live_out / "stage").is_dir():
        return False, f"실제 stage 를 찾지 못했다: {live_out/'stage'}"
    cfgp = REPO / "config" / "harness.yaml"
    t = cfgp.read_text(encoding="utf-8")
    want = str(live_out)
    if f'workspace: "{want}"' in t:
        return True, "이미 가리키고 있다"
    if dry:
        return True, f"(예정) operator.workspace → {want}"
    t2 = re.sub(r'^(\s*workspace:\s*).*$', lambda m: m.group(1) + f'"{want}"',
                t, count=1, flags=re.M)
    if t2 == t:
        return False, "harness.yaml 에 workspace 줄을 찾지 못했다"
    cfgp.write_text(t2, encoding="utf-8")
    rc = subprocess.run([sys.executable, str(REPO / "install" / "configure.py"), "--build"],
                        capture_output=True, text=True, timeout=300)
    if rc.returncode != 0:
        return False, f"재빌드 실패: {(rc.stdout+rc.stderr)[-150:]}"
    return True, f"workspace → {want} · 재빌드"


# ── 단계 3 · 승격 이관 ──────────────────────────────────────────────────────
def phase3(dry: bool) -> tuple[bool, str]:
    """막아 둔 ssot-promote 를 옮긴다. 선행조건(단계 2)이 풀린 뒤에만."""
    mig = REPO / "install" / "migrate.py"
    mp = paths.state / "migration.json"
    st = {}
    if mp.is_file():
        try:
            st = json.loads(mp.read_text(encoding="utf-8"))
        except ValueError:
            st = {}
    st.setdefault("done", {})
    st.setdefault("blocked", {})
    # 선행조건이 풀렸으니 막아 둔 것을 푼다
    st["blocked"].pop("ssot-promote", None)
    mp.parent.mkdir(parents=True, exist_ok=True)
    mp.write_text(json.dumps(st, ensure_ascii=False, indent=1), encoding="utf-8")
    cmd = [sys.executable, str(mig), "--step", "--only", "ssot-promote"]
    if dry:
        cmd.append("--dry-run")
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=900)
    tail = [l for l in (r.stdout + r.stderr).splitlines() if l.strip()][-2:]
    return r.returncode == 0, " / ".join(x.strip() for x in tail)


# ── 단계 4 · 상시 위반 ──────────────────────────────────────────────────────
def phase4(dry: bool) -> tuple[bool, str]:
    """고치지 않는다. **사람이 판단할 것을 모아 보고**한다.

    남은 것들은 권한 대기·마운트 수렴처럼 코드로 닫을 수 없는 것들이다.
    자동으로 「통과」시키면 그때부터 게이트는 거짓말을 한다.
    """
    _rc, items = gate()
    rest = owned_by(PHASES[3][2], items)
    rep = paths.state / "상시위반.md"
    lines = [f"# 게이트 상시 위반 — {time.strftime('%Y-%m-%d %H:%M')}", "",
             "코드로 닫을 수 없는 것들이다. 사람이 판단한다.", ""]
    for mark, name, d in rest:
        lines += [f"## {mark} {name}", f"- {d}", ""]
    # 사람이 적은 조사 메모는 **지우지 않는다.** 게이트 한 줄은 증상이고,
    # 왜 그런지는 메모에 있다. 다시 돌릴 때마다 날아가면 아무도 안 적는다
    keep = ""
    if rep.exists():
        prev = rep.read_text(encoding="utf-8")
        if MEMO in prev:
            keep = prev[prev.index(MEMO):]
    if not keep:
        keep = MEMO + "\n\n(아직 없음 — 조사한 것을 여기에 적는다)\n"
    if not dry:
        rep.parent.mkdir(parents=True, exist_ok=True)
        rep.write_text("\n".join(lines) + "\n" + keep, encoding="utf-8")
    return True, f"{len(rest)}건 정리 → {rep.name}"


RUNNERS = {"1-정의단일화": phase1, "2-데이터경로": phase2,
           "3-승격이관": phase3, "4-상시위반": phase4}


def cmd_check() -> int:
    rc, items = gate()
    f = failing(items)
    print(f"게이트: 통과 {len(items)-len(f)} · 실패·차단 {len(f)} / 총 {len(items)}\n")
    for ph, desc, keys in PHASES:
        mine = owned_by(keys, items) if keys else []
        print(f"  [{ph}] {desc}")
        if not keys:
            print("      (게이트 항목이 아니라 선행조건)")
        elif not mine:
            print("      ✓ 이 단계가 닫을 항목 없음")
        else:
            for mark, name, d in mine:
                print(f"      {mark} {name} — {d[:72]}")
        print()
    return 0 if rc == 0 else 1


def cmd_plan() -> int:
    st = load_state()
    rc, items = gate()
    print(f"복구 계획 — 4단계 · 끝남 {len(st['done'])}\n")
    for ph, desc, keys in PHASES:
        mine = owned_by(keys, items) if keys else []
        done = ph in st["done"]
        mark = "✓" if done else ("▶" if not done and ph == next_phase(st) else "·")
        print(f"  {mark} [{ph}] {desc}")
        if keys:
            print(f"      닫을 항목 {len(mine)}건" + (f" — {mine[0][1][:50]} …" if mine else ""))
        if done:
            print(f"      {st['done'][ph].get('note','')[:80]}")
    print(f"\n  다음: {next_phase(st) or '없음'}")
    print("  각 단계는 게이트로 검증한다. 닫히지 않으면 다음으로 가지 않는다")
    return 0


def next_phase(st: dict) -> str | None:
    for ph, _d, _k in PHASES:
        if ph not in st["done"]:
            return ph
    return None


def cmd_step(dry: bool, only: str | None) -> int:
    st = load_state()
    ph = only or next_phase(st)
    if not ph:
        print("4단계 전부 끝났다.")
        return 0
    desc, keys = next((d, k) for p, d, k in PHASES if p == ph)
    print(f"▶ [{ph}] {desc}")

    rc0, items0 = gate()
    before = owned_by(keys, items0) if keys else []
    total0 = len(items0)
    if keys:
        print(f"  전: 이 단계가 닫을 항목 {len(before)}건 (게이트 총 {total0}항목)")

    ok, msg = RUNNERS[ph](dry)
    print(f"  작업 … {'OK' if ok else '실패'} — {msg}")
    if not ok:
        return 2
    if dry:
        print("\n  [dry-run] 실제로는 바꾸지 않았다")
        return 0

    # 검증 — 그 단계가 닫아야 할 항목이 실제로 닫혔는가
    if keys:
        _rc1, items1 = gate()
        after = owned_by(keys, items1)
        total1 = len(items1)
        print(f"  후: {len(after)}건 남음 (게이트 총 {total1}항목)")
        # **항목이 사라진 것은 닫힌 것이 아니다.**
        # 게이트가 예외로 죽으면 그 블록의 검사가 통째로 빠지고, 실패 수만 보면
        # 「고쳐졌다」로 읽힌다 — 2026-10-22 실측: 26→20 으로 줄었는데 통과로 쳤다
        if total1 < total0:
            print(f"\n  ✗ 게이트 항목이 {total0}→{total1} 로 **줄었다.**")
            print("     검사가 사라진 것이지 고쳐진 것이 아니다. 게이트가 죽는지 먼저 본다")
            return 1
        if REPORT_ONLY.get(ph):
            # 이 단계는 고치는 단계가 아니라 **보고하는 단계**다.
            # 남은 것들은 권한 대기·외부 수렴처럼 코드로 닫을 수 없다. 그래도
            # 항목은 게이트에 빨간불로 남는다 — 보고했다고 통과로 바꾸지 않는다.
            # 보고했다는 말을 믿지 않는다. 보고서를 읽어 대조한다
            rep = (paths.state / "상시위반.md")
            body = rep.read_text(encoding="utf-8") if rep.exists() else ""
            missing = [n for _m, n, _d in after if n not in body]
            print(f"  후: {len(after)}건 남음 — 보고 단계이므로 **닫지 않는다**")
            for mark, name, d in after:
                print(f"      {mark} {name} — {d[:70]}")
            if missing:
                print(f"\n  ✗ 보고서에 빠진 항목 {len(missing)}건: {missing}")
                return 1
        elif len(after) >= len(before) and before:
            for mark, name, d in after:
                print(f"      {mark} {name} — {d[:70]}")
            print("\n  닫히지 않았다. 다음 단계로 가지 않는다")
            return 1

    st["done"][ph] = {"at": time.strftime("%Y-%m-%dT%H:%M:%S"), "note": msg}
    save_state(st)
    nxt = next_phase(st)
    print(f"\n  ✓ {ph} 완료" + (f" · 다음: {nxt}" if nxt else " — 전부 끝났다"))
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--plan", action="store_true")
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--step", action="store_true")
    ap.add_argument("--only")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    if a.check:
        return cmd_check()
    if a.step or a.only:
        return cmd_step(a.dry_run, a.only)
    return cmd_plan()


if __name__ == "__main__":
    raise SystemExit(main())
