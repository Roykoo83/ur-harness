#!/usr/bin/env python3
"""`2-7. 각 사업 예산 현황`의 최신 파일을 예산 시스템(JSON)으로 동기화한다.

    python3 scripts/sync_budget_status.py

- 원천: {{MEMBER_A}} 매니저가 **주단위로** 올리는 시스템 등록 최신본 (파일명의 YYMMDD 가 기준)
- 확인: **일단위** — tasks_hourly(3시간) 사이클에 붙어 돌지만, 파일명 날짜가
  같으면 아무것도 하지 않는다 (멱등)
- 산출: data/예산현황/<YYMMDD>.json (원본 스냅샷) + latest.json (포인터)

파서는 보수적이다 — 시트 구조가 과제마다 다르므로 **숫자가 있는 행을 그대로 보존**하고
해석(비목 분류)은 소비하는 쪽이 한다. 원본을 잃는 가공은 하지 않는다.
"""
from __future__ import annotations

import json
import re
import unicodedata
from pathlib import Path

import openpyxl

HOME = Path.home()
SRC = (HOME / "{{MOUNT_REL}}"
       / "공유 드라이브/U/2. R&D 사업/2-7. 각 사업 예산 현황")
OUT = Path(__file__).resolve().parents[1] / "data" / "예산현황"
# 2026-09-05: 마운트 쓰기는 유지하되 **도착을 API 해시로 검증**한다(lib/ssot_io.verify_dir).
# 업로드 rc=0 은 도착의 증거가 아니다 — "☁ 반영"이 거짓이던 지점.
SSOT_OUT = (HOME / "{{MOUNT_REL}}"
            / "공유 드라이브/{{SSOT_DRIVE}}/{{DEPT_ROOT}}/20_예산/10_시스템등록_예산현황")


def nfc(s: str) -> str:
    return unicodedata.normalize("NFC", s)


def latest_source() -> tuple[Path, str] | None:
    """파일명 YYMMDD 가 가장 큰 「예산 현황」 파일. mtime 이 아니라 **파일명 날짜**가
    기준이다 — 드라이브 동기화가 mtime 을 흔든다."""
    best = None
    for f in SRC.glob("*.xlsx"):
        name = nfc(f.name)
        if "예산 현황" not in name and "예산현황" not in name:
            continue
        m = re.search(r"(\d{6})", name)
        if not m:
            continue
        if best is None or m.group(1) > best[1]:
            best = (f, m.group(1))
    return best


def snapshot(f: Path) -> dict:
    # 클라우드 마운트의 자리표시자는 아직 내려받히지 않아 zip 이 아니다.
    # 한 파일 때문에 서비스 전체가 죽으면 나머지 예산도 갱신되지 않는다 —
    # 그 파일만 건너뛰고 **건너뛴 사실을 남긴다**(조용히 지나가지 않는다).
    try:
        with f.open("rb") as _fh:
            if _fh.read(2) != b"PK":
                print(f"! 건너뜀(아직 내려받히지 않음): {f.name}", flush=True)
                return None
    except OSError as e:
        print(f"! 건너뜀(읽기 실패 {type(e).__name__}): {f.name}", flush=True)
        return None
    try:
        wb = openpyxl.load_workbook(f, data_only=True)
    except Exception as e:                                   # noqa: BLE001
        print(f"! 건너뜀(열지 못함 {type(e).__name__}): {f.name}", flush=True)
        return None
    sheets = {}
    for name in wb.sheetnames:
        ws = wb[name]
        rows = []
        for r in ws.iter_rows(values_only=True):
            cells = [("#REF!" if isinstance(c, str) and c.startswith("#REF") else c)
                     for c in r]
            if any(c not in (None, "") for c in cells):
                rows.append([("" if c is None else c) for c in cells])
        sheets[nfc(name).strip()] = rows
    return sheets


def main() -> int:
    src = latest_source()
    if not src:
        print("원천 파일 없음:", SRC)
        return 2
    f, ymd = src
    OUT.mkdir(parents=True, exist_ok=True)
    target = OUT / f"{ymd}.json"
    ptr = OUT / "latest.json"

    if target.exists():
        cur = json.loads(ptr.read_text(encoding="utf-8")) if ptr.exists() else {}
        if cur.get("date") == ymd:
            print(f"· 변경 없음 — 최신 {ymd} 이미 반영")
            return 0

    sheets = snapshot(f)
    if sheets is None:
        # 원본을 읽지 못했다. **빈 것으로 덮어쓰지 않는다** —
        # 빈 스냅샷을 쓰면 다음 단계가 "예산 0건"을 사실로 읽는다
        print(f"✗ 원본을 읽지 못해 갱신하지 않는다: {nfc(f.name)}\n"
              f"  클라우드 마운트가 아직 내려받지 않았을 수 있다. "
              f"파일을 한 번 열어 받은 뒤 다시 돌린다", flush=True)
        return 1
    data = {"date": ymd, "source_file": nfc(f.name), "sheets": sheets}
    target.write_text(json.dumps(data, ensure_ascii=False, default=str), encoding="utf-8")
    ptr.write_text(json.dumps({"date": ymd, "file": target.name,
                               "source": nfc(f.name)}, ensure_ascii=False), encoding="utf-8")
    n = sum(len(v) for v in data["sheets"].values())
    print(f"· 동기화 — {nfc(f.name)} → {target.name} (시트 {len(data['sheets'])}·행 {n})")
    # 정규 데이터셋 재구축 + SSOT 게시 (build_budget_dataset.py 가 담당)
    import subprocess, sys as _s
    subprocess.run([_s.executable, str(Path(__file__).parent / "build_budget_dataset.py")],
                   check=False)

    # 원본 품질 경고 — 고치지 않고 알리기만 한다 (원천은 읽기 전용)
    for s, rows in data["sheets"].items():
        refs = sum(1 for r in rows for c in r if c == "#REF!")
        if refs:
            print(f"  ⚠ {s}: #REF! {refs}개 — 원본 수식 깨짐, {{MEMBER_A}}님 확인 필요")
        if len(rows) <= 1:
            print(f"  ⚠ {s}: 비어 있음")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
