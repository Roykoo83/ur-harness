#!/usr/bin/env python3
"""skills_manifest.py: 도구상자 배포 목록을 실제 폴더에서 생성한다.

  python3 skills_manifest.py                 목록을 만들어 SSOT 에 쓴다
  python3 skills_manifest.py --check         VERSION 누락·불일치만 점검
  python3 skills_manifest.py --print         화면에만 출력

손으로 쓴 목록은 썩는다. 스킬 폴더의 VERSION 파일을 읽어 매번 다시 만든다.
"""
import argparse
import io
import os
import re
import subprocess
import sys
from datetime import date

TD = "{{SSOT_DRIVE_ID}}"
REMOTE = "gdrive:50_도구상자/10_스킬"
FIELDS = ["name", "version", "배포일", "출처", "라이선스", "담당", "용도",
          "검증", "미검증", "사용제한", "상류", "주의"]


def sh(args):
    return subprocess.run(args, capture_output=True, text=True).stdout


def skills():
    out = sh(["rclone", "lsf", REMOTE, "--dirs-only", "--drive-team-drive", TD])
    return sorted(d.strip("/") for d in out.split("\n") if d.strip() and not d.startswith("_"))


def read_version(name, tmp):
    dst = os.path.join(tmp, name)
    os.makedirs(dst, exist_ok=True)
    subprocess.run(["rclone", "copy", f"{REMOTE}/{name}/VERSION", dst,
                    "--drive-team-drive", TD], capture_output=True)
    p = os.path.join(dst, "VERSION")
    if not os.path.isfile(p):
        return None
    d, key = {}, None
    for line in io.open(p, encoding="utf-8"):
        if not line.strip():          # 빈 줄에서 필드가 끝난다
            key = None
            continue
        m = re.match(r"^(\S[^:]*):\s*(.*)$", line)
        if m and m.group(1) in FIELDS:
            key = m.group(1); d[key] = m.group(2).strip()
        elif m and not line.startswith((" ", "\t")):
            key = None                # 우리가 안 읽는 필드가 시작되면 이어붙이지 않는다
        elif key and line.strip():
            d[key] = (d[key] + " " + line.strip()).strip()
    return d


def badge(v):
    """사용제한이 있으면 눈에 띄게 표시한다. 폴더 안에만 있으면 아무도 안 본다."""
    lim = (v or {}).get("사용제한", "")
    if not lim or lim == "없음":
        return ""
    if "비영리" in lim:
        return " **[비영리 전용]**"
    return " **[제한 있음]**"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--print", dest="show", action="store_true")
    a = ap.parse_args()

    import tempfile
    tmp = tempfile.mkdtemp(prefix="skillver_")
    rows, missing = [], []
    for s in skills():
        v = read_version(s, tmp)
        if not v:
            missing.append(s)
        rows.append((s, v))

    if a.check:
        if missing:
            print("[!!] VERSION 없는 스킬:", ", ".join(missing)); sys.exit(1)
        print(f"[OK] 스킬 {len(rows)}개 전부 VERSION 보유")
        for s, v in rows:
            lim = v.get("사용제한", "")
            if lim and lim != "없음":
                print(f"     [제한] {s}: {lim[:60]}")
        sys.exit(0)

    lines = [
        "---",
        'title: "도구상자 배포 목록"',
        f'생성: "{date.today()} · skills_manifest.py 자동 생성"',
        '주의: "손으로 고치지 마라. 각 스킬의 VERSION 파일을 고치고 이 도구를 다시 돌려라"',
        "---", "",
        "# 도구상자 배포 목록", "",
        f"스킬 {len(rows)}개. 각 폴더의 `VERSION` 을 읽어 만들었다.", "",
        "| 스킬 | 버전 | 배포일 | 출처 | 라이선스 | 용도 |",
        "|---|---|---|---|---|---|",
    ]
    for s, v in rows:
        v = v or {}
        lines.append("| **{}**{} | `{}` | {} | {} | {} | {} |".format(
            s, badge(v), v.get("version", "없음"), v.get("배포일", "-"),
            v.get("출처", "-"), v.get("라이선스", "-"), v.get("용도", "-")[:40]))

    note_rows = [(s, v) for s, v in rows if v and v.get("주의")]
    if note_rows:
        lines += ["", "## 쓸 때 주의할 것", ""]
        for s, v in note_rows:
            lines += [f"- **{s}** · {v['주의']}"]

    lim_rows = [(s, v) for s, v in rows if v and v.get("사용제한", "없음") != "없음"]
    if lim_rows:
        lines += ["", "## 사용 제한이 있는 것", "",
                  "**설치 전에 읽어라.** 폴더 안 문서만으로는 눈에 띄지 않는다.", ""]
        for s, v in lim_rows:
            lines += [f"### {s}", "", v["사용제한"], ""]

    lines += ["", "## 검증 상태", "",
              "| 스킬 | 검증됨 | 미검증 |", "|---|---|---|"]
    for s, v in rows:
        v = v or {}
        lines.append(f"| {s} | {v.get('검증','-')[:50]} | {v.get('미검증','-')[:40]} |")

    if missing:
        lines += ["", "## VERSION 이 없는 스킬", ""]
        lines += [f"- {m}" for m in missing]

    out = "\n".join(lines) + "\n"
    if a.show:
        print(out); return
    p = os.path.join(tmp, "_배포목록.md")
    io.open(p, "w", encoding="utf-8").write(out)
    subprocess.run(["rclone", "copy", p, REMOTE, "--drive-team-drive", TD],
                   capture_output=True)
    print(f"[OK] _배포목록.md 생성 · 스킬 {len(rows)}개"
          + (f" · VERSION 없음 {len(missing)}개" if missing else ""))


if __name__ == "__main__":
    main()
