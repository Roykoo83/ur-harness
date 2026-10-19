#!/usr/bin/env python3
"""Drive 중복 정리 — 「이름 N」 사본 + 동명 파일. M 안 (운영자 결정 2026-09-07)

중복은 두 계층에 있다:
  1) 「이름 N」(N=2..9) — DriveFS 가 충돌을 렌더링한 사본. 정본과 MD5 로 대조한다.
  2) 동명 파일 — 같은 폴더에 같은 이름이 둘. **API 목록에만 보인다** (DriveFS 는 하나를
     「이름 2」로 보여준다). 오전 정리가 이 계층을 통째로 놓쳤다 — 9경로.
     `rclone dedupe` 로 처리한다 (내용 동일은 하나만 남기고, 다르면 newest 유지).

구역 규칙(M): **기계 구역만 API 로 지운다.** 사람 구역의 중복은 목록만 내고, 사람이 마운트에서
지운다 — 거기는 마운트가 쓰는 곳이라 API 로 지우면 DriveFS 가 되살린다 (12:25 실측).
예외: `.DS_Store*` 는 어디서든 지운다 (macOS 쓰레기).

판정은 MD5 로만. 정본이 없으면 그것은 원래 파일명이다 → 건드리지 않는다.
삭제는 Drive 휴지통 (30일 복구).

사용: drive_dedupe.py [--apply]   (기본은 dry-run)
"""
from __future__ import annotations
import argparse, collections, os, re, subprocess, sys, unicodedata
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from lib.ssot_io import in_machine_zone                     # noqa: E402

RO, RW = "SSOT_ro:", "SSOT_rw:"
# NFC 로 정규화해 비교하되, rclone 에 넘길 때는 Drive 에 있는 **원문**(NFD 일 수 있음)을 쓴다.
# 2026-09-07: NFC 로 넘긴 deletefile 이 「 3.pptx」(맥 생성, NFD)를 "없는 파일"로 봤다.
ORIG: dict[str, str] = {}
# DriveFS 충돌 렌더 세 꼴 — 「 2.ext」 「 2 (1).ext」 「 (2).ext」 (2026-09-08 실측). 정본 = 접미를 뗀 이름.
DUP_RE = re.compile(r"^(?P<stem>.+?)(?: (?P<n>[2-9])(?: \(\d+\))?| \((?P<m>\d+)\))(?P<ext>\.[A-Za-z0-9]{1,8})?$")


def nfc(s: str) -> str:
    return unicodedata.normalize("NFC", s)


def run(args: list[str], timeout=900) -> subprocess.CompletedProcess:
    return subprocess.run(args, capture_output=True, text=True, timeout=timeout)


def listing() -> tuple[dict[str, list[str]], list[str]]:
    """path → [md5, …] (동명이면 2개 이상), 그리고 폴더 목록. 마운트는 읽지 않는다."""
    p = run(["rclone", "lsf", "-R", "--files-only", "--format", "ph",
             "--hash", "MD5", "--fast-list", RO])
    if p.returncode != 0:
        raise RuntimeError(f"lsf rc={p.returncode}: {p.stderr[-200:]}")
    H: dict[str, list[str]] = collections.defaultdict(list)
    for line in p.stdout.splitlines():
        if ";" in line:
            path, md5 = line.rsplit(";", 1)
            H[nfc(path)].append(md5)
            ORIG.setdefault(nfc(path), path)      # 삭제 명령은 Drive 의 원문 이름으로
    d = run(["rclone", "lsf", "-R", "--dirs-only", "--fast-list", RO])
    D = []
    for x in d.stdout.splitlines():
        if x.strip():
            raw = x.rstrip("/"); D.append(nfc(raw)); ORIG.setdefault(nfc(raw), raw)
    return H, D


def canonical(dup: str) -> str:
    head, base = os.path.split(dup)
    m = DUP_RE.match(base)
    return os.path.join(head, m["stem"] + (m["ext"] or "")) if m else ""


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="실제 삭제 (기본은 공회전)")
    a = ap.parse_args()

    H, D = listing()
    one = {p: h[0] for p, h in H.items()}      # 동명이 아닐 때의 해시
    plan: list[tuple[str, str, str]] = []      # (kind, target, 사유)  kind: dir|file|dedupe
    human: list[tuple[str, str]] = []          # 사람 구역 — 목록만
    keep: list[tuple[str, str]] = []

    # ── 1. 「이름 N」 폴더 ────────────────────────────────────────
    Dset = set(D)
    for d in sorted(D):
        c = canonical(d)
        if not c or c not in Dset:
            continue
        inner = {p[len(d) + 1:]: one[p] for p in one if p.startswith(d + "/")}
        bad = [r for r, h in inner.items() if one.get(f"{c}/{r}") != h]
        why = f"{len(inner)}건 전부 정본과 MD5 일치" if not bad else f"사본 {len(inner)}건 중 {len(bad)}건이 정본에 없거나 다름"
        if bad:
            keep.append((d, why)); continue
        (plan if in_machine_zone(d + "/") else human).append(
            ("dir", d, why) if in_machine_zone(d + "/") else (d, "폴더 · " + why))

    covered = tuple(p + "/" for k, p, _ in plan if k == "dir") + tuple(p + "/" for p, _ in human)

    # ── 2. 「이름 N」 파일 + .DS_Store ────────────────────────────
    for f in sorted(one):
        if f.startswith(covered):
            continue
        base = os.path.basename(f)
        if base.startswith(".DS_Store"):
            plan.append(("file", f, "macOS 쓰레기")); continue
        c = canonical(f)
        if not c:
            continue
        if c not in one:
            keep.append((f, "정본 없음 → 원래 파일명")); continue
        if one[c] != one[f]:
            keep.append((f, f"★내용 다름 (정본 {one[c][:8]} vs 사본 {one[f][:8]}) — 사람이 판단")); continue
        if in_machine_zone(f):
            plan.append(("file", f, f"MD5 일치 {one[f][:8]}"))
        else:
            human.append((f, f"MD5 일치 {one[f][:8]} — 마운트에서 지운다"))

    # ── 3. 동명 파일 (API 에서만 보임) ─────────────────────────────
    by_dir: dict[str, list[tuple[str, int, bool]]] = collections.defaultdict(list)
    for p, hs in H.items():
        if len(hs) > 1:
            by_dir[os.path.dirname(p)].append((os.path.basename(p), len(hs), len(set(hs)) == 1))
    for d, items in sorted(by_dir.items()):
        desc = " · ".join(f"{n}×{k}{'' if same else '★다름'}" for n, k, same in items)
        if in_machine_zone(d + "/"):
            plan.append(("dedupe", d, f"동명 {len(items)}경로 — {desc[:90]}"))
        else:
            human.append((d, f"동명 {len(items)}경로 (마운트에서 「이름 2」로 보임) — {desc[:80]}"))

    # ── 출력 ─────────────────────────────────────────────────────
    print(f"{'조치':<6}{'종류':<7}{'경로':<72}사유")
    print("─" * 120)
    for k, p, why in plan:
        print(f"{'삭제':<6}{k:<7}{p[-70:]:<72}{why}")
    for p, why in human:
        print(f"{'사람':<6}{'':<7}{p[-70:]:<72}{why}")
    for p, why in keep:
        print(f"{'보존':<6}{'':<7}{p[-70:]:<72}{why}")
    nd = sum(1 for k, *_ in plan if k == "dir"); nf = sum(1 for k, *_ in plan if k == "file")
    ns = sum(1 for k, *_ in plan if k == "dedupe")
    print(f"\nAPI 삭제(기계 구역): 폴더 {nd} · 파일 {nf} · 동명 dedupe {ns}폴더   |   사람 구역(마운트에서): {len(human)}   |   보존 {len(keep)}")

    if not a.apply:
        print("\n[dry-run] --apply 를 붙이면 Drive 휴지통으로 이동합니다 (30일 복구 가능).")
        return 0

    fail = 0
    for k, p, _ in plan:
        if k == "dedupe":
            # 내용 동일은 자동으로 하나만 남기고, 다르면 newest 를 남긴다.
            cmd = ["rclone", "dedupe", "newest", f"{RW}{ORIG.get(p, p)}"]
        else:
            cmd = ["rclone", "purge" if k == "dir" else "deletefile", f"{RW}{ORIG.get(p, p)}"]
        r = run(cmd, timeout=300)
        ok = r.returncode == 0
        fail += 0 if ok else 1
        print(f"{'✓' if ok else '✗'} {k} {p}" + ("" if ok else f"  rc={r.returncode} {r.stderr.strip()[-140:]}"))
    print(f"\n완료 — 실패 {fail}건")
    return 1 if fail else 0


if __name__ == "__main__":
    raise SystemExit(main())
