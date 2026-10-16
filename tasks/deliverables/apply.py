#!/usr/bin/env python3
"""후보표에서 체크된 것만 U로 복사한다 (파이프라인 ⑥⑦). 사람이 실행한다.

    python3 scripts/deliverables/apply.py [후보표.md] [--dry]

후보표 표기:
    - [x] ...   복사한다
    - [-] ...   앞으로 다시 올리지 않는다 (decided 에 ignored 로 기록)
    - [ ] ...   이번엔 건너뛴다 (다음 스캔에 다시 올라온다)

규칙 (2026-09-04 실패에서 나온 것):
  · 복사 동시성은 1. 병렬은 동명 중복 폴더를 만든다
  · 상위 폴더를 먼저 만든다
  · 복사가 끝나면 U 를 다시 조회해 검증한다
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import re
import subprocess
import sys
import unicodedata
from pathlib import Path

PROJ = Path(__file__).resolve().parents[2]
CFG = json.loads((Path(__file__).parent / "config.json").read_text(encoding="utf-8"))
DATA = PROJ / "data" / "deliverables"
OUT = PROJ / "output" / "deliverables"
DECIDED = DATA / "decided.jsonl"
UU = f"gdrive,team_drive={CFG['drives']['u']}:"
SS = f"gdrive,team_drive={CFG['drives']['ssot']}:"
COMMON = ["--drive-server-side-across-configs", "--retries", "5", "--low-level-retries", "10"]

ITEM = re.compile(r"^- \[(?P<mark>[ x\-])\] `(?P<md5>[0-9a-f]{8})`")
SRC = re.compile(r"^\s+원천: `(?P<src>.+)`\s*$")
DST = re.compile(r"^\s+>> `(?P<dst>.+)`\s*$")


def nfc(s): return unicodedata.normalize("NFC", s)


def log(m): print(f"[{dt.datetime.now():%H:%M:%S}] {m}", flush=True)


def parse(path: Path) -> list[dict]:
    items, cur = [], None
    for ln in path.read_text(encoding="utf-8").splitlines():
        m = ITEM.match(ln)
        if m:
            if cur:
                items.append(cur)
            cur = {"mark": m["mark"], "md5": m["md5"], "src": None, "dst": None}
            continue
        if not cur:
            continue
        if (s := SRC.match(ln)):
            cur["src"] = s["src"]
        elif (d := DST.match(ln)):
            cur["dst"] = d["dst"]
    if cur:
        items.append(cur)
    return items


def record(md5: str, action: str, src: str, dst: str | None) -> None:
    DATA.mkdir(parents=True, exist_ok=True)
    with DECIDED.open("a", encoding="utf-8") as f:
        f.write(json.dumps({"md5": md5, "action": action, "src": src, "dst": dst,
                            "at": dt.datetime.now().isoformat(timespec="seconds")},
                           ensure_ascii=False) + "\n")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("table", nargs="?", help="후보표 경로 (생략 시 가장 최근)")
    ap.add_argument("--dry", action="store_true")
    a = ap.parse_args()

    path = Path(a.table) if a.table else max(OUT.glob("후보표_*.md"), default=None)
    if not path or not path.exists():
        log("후보표가 없다"); return 2

    items = parse(path)
    todo = [i for i in items if i["mark"] == "x"]
    skip = [i for i in items if i["mark"] == "-"]
    log(f"{path.name}: 복사 {len(todo)} · 무시 {len(skip)} · 보류 {len(items)-len(todo)-len(skip)}")

    bad = [i for i in todo if not i["dst"] or "(정하세요)" in i["dst"]]
    if bad:
        for i in bad:
            log(f"  목적지 미정: {i['src']}")
        log("목적지를 채운 뒤 다시 실행한다"); return 1

    if a.dry:
        for i in todo:
            log(f"  (dry) {i['src']}  >>  {i['dst']}")
        return 0

    for i in skip:
        record(i["md5"], "ignored", i["src"], None)

    # 상위 폴더 먼저 (동시 생성 경합 방지)
    for d in sorted({i["dst"].rsplit("/", 1)[0] for i in todo}):
        subprocess.run(["rclone", "mkdir", UU + d], capture_output=True, text=True)

    ok, fail = [], []
    for n, i in enumerate(todo, 1):
        src = i["src"]
        source = src if src.startswith("/") else SS + src   # 절대경로면 로컬, 아니면 SSOT
        r = subprocess.run(["rclone", "copyto", source, UU + i["dst"]] + COMMON,
                           capture_output=True, text=True)
        if r.returncode == 0:
            ok.append(i); record(i["md5"], "copied", src, i["dst"])
        else:
            fail.append((i, (r.stderr or "").strip().splitlines()[-1:]))
        log(f"  {n}/{len(todo)} {'OK ' if r.returncode == 0 else '실패'} {i['dst'].rsplit('/', 1)[-1][:60]}")

    log(f"\n복사 성공 {len(ok)} · 실패 {len(fail)}")
    for i, e in fail:
        log(f"  실패: {i['dst']} | {(e[0] if e else '')[:140]}")

    # ⑦ 검증 - 대상 폴더만 재조회
    log("검증")
    miss = []
    for d in sorted({i["dst"].rsplit("/", 1)[0] for i in ok}):
        r = subprocess.run(["rclone", "lsf", "--files-only", UU + d],
                           capture_output=True, text=True)
        have = {nfc(x) for x in (r.stdout or "").splitlines()}
        for i in ok:
            if i["dst"].rsplit("/", 1)[0] == d and nfc(i["dst"].rsplit("/", 1)[-1]) not in have:
                miss.append(i["dst"])
    log("검증 통과" if not miss else f"검증 실패 {len(miss)}건: " + " / ".join(miss[:5]))
    return 0 if not fail and not miss else 1


if __name__ == "__main__":
    raise SystemExit(main())
