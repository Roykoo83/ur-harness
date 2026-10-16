#!/usr/bin/env python3
"""selftest.py: 회의록 워크플로우 회귀 시험. 고치고 나서 이걸 돌린다.

  python3 selftest.py            (전사 없이 구조·경계만)
  python3 selftest.py --live     (Groq 실호출 포함. 무료 한도를 조금 쓴다)
"""
import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
SKILL = HERE.parent
PY = sys.executable or "python3"
results = []


def check(name, cond, detail=""):
    results.append((name, bool(cond), detail))
    print(f"  {'OK ' if cond else '!! '}{name}" + (f"  {detail}" if detail and not cond else ""))
    return cond


def run(args, **kw):
    return subprocess.run([PY] + [str(x) for x in args],
                          capture_output=True, text=True, **kw)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--live", action="store_true", help="Groq 실호출 포함")
    a = ap.parse_args()
    tmp = Path(tempfile.mkdtemp(prefix="minutes_selftest_"))
    sample = SKILL / "assets" / "샘플_한국어_시험음성.m4a"

    print("\n[1] 문법")
    for f in sorted((HERE).glob("*.py")):
        r = run(["-c", f"import ast,io;ast.parse(io.open(r'{f}',encoding='utf-8').read())"])
        check(f"{f.name} 파싱", r.returncode == 0, r.stderr.strip()[:120])

    print("\n[2] 환경")
    r = run([HERE / "transcribe.py", "--doctor"])
    check("doctor 실행", r.returncode in (0, 1))
    check("ffmpeg 있음", shutil.which("ffmpeg") is not None, "brew/winget 으로 설치")

    print("\n[3] 입력 방어")
    r = run([HERE / "prepare_audio.py", "/없는/파일.mp4", "--out", tmp / "a"])
    check("없는 파일 거부", "파일 없음" in r.stdout + r.stderr)
    r = run([HERE / "prepare_audio.py", sample, "--out", tmp / "b", "--max-mb", "0"])
    check("--max-mb 0 거부", r.returncode != 0)
    r = run([HERE / "prepare_audio.py", sample, "--out", tmp / "c", "--bitrate", "999"])
    check("--bitrate 범위 거부", r.returncode != 0)

    print("\n[4] 전처리")
    r = run([HERE / "prepare_audio.py", sample, "--out", tmp / "ok"])
    ok = r.returncode == 0
    check("샘플 전처리", ok, r.stderr.strip()[:160])
    if ok:
        plan = json.loads((tmp / "ok" / "_전송용" / "_전송계획.json").read_text(encoding="utf-8"))
        tot = sum(c["dur_s"] for c in plan["chunks"])
        check("길이 보존", abs(tot - plan["duration_s"]) < 1.0,
              f"{tot} vs {plan['duration_s']}")
        prev, cont = 0.0, True
        for c in plan["chunks"]:
            if abs(c["offset_s"] - prev) > 0.05:
                cont = False
            prev = c["offset_s"] + c["dur_s"]
        check("조각 연속성", cont)

    print("\n[5] 분할과 크기 재검증")
    # 짧은 샘플은 컨테이너 오버헤드 때문에 분할 시험이 성립하지 않는다.
    # 시험용 3분짜리 음성을 만들어 쓴다.
    long_src = tmp / "long.wav"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi",
                    "-i", "sine=frequency=220:duration=180",
                    "-ar", "16000", "-ac", "1", str(long_src)], check=True)
    LIMIT = 0.12
    r = run([HERE / "prepare_audio.py", long_src, "--out", tmp / "sp",
             "--max-mb", str(LIMIT)])
    if r.returncode == 0:
        plan = json.loads((tmp / "sp" / "_전송용" / "_전송계획.json").read_text(encoding="utf-8"))
        over = [c for c in plan["chunks"] if os.path.getsize(c["file"]) > LIMIT * 1e6]
        check("모든 조각이 한도 이하", not over, f"{len(over)}개 초과")
        check("복수 조각 생성", len(plan["chunks"]) > 1, f"{len(plan['chunks'])}개")
        # 강제 분할 경계는 4초씩 겹치게 만든다. 그만큼 합이 길어지는 것이 정상이다.
        tot = sum(c["dur_s"] for c in plan["chunks"])
        forced = sum(1 for c in plan["chunks"] if c.get("forced_cut"))
        expect = plan["duration_s"] + forced * 4.0
        check("분할 후 길이 보존 (겹침 감안)", abs(tot - expect) < 1.5,
              f"합 {tot:.1f} / 기대 {expect:.1f} (강제분할 {forced}곳)")
        # 겹침 구간은 전사 후 중복 제거 대상이다. 빈틈이 없어야 한다.
        prev, gap_ok = 0.0, True
        for c in plan["chunks"]:
            if c["offset_s"] > prev + 0.05:
                gap_ok = False
            prev = max(prev, c["offset_s"] + c["dur_s"])
        check("분할 후 빈틈 없음", gap_ok)
    else:
        check("분할 실행", False, (r.stdout + r.stderr).strip()[-160:])
    r = run([HERE / "prepare_audio.py", sample, "--out", tmp / "imp", "--max-mb", "0.001"])
    check("불가능한 한도는 깨끗이 거부",
          r.returncode != 0 and "줄이지 못했다" in (r.stdout + r.stderr))

    print("\n[6] 무음 감지")
    sil = tmp / "silent.ogg"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi",
                    "-i", "anullsrc=r=16000:cl=mono", "-t", "40",
                    "-c:a", "libopus", "-b:a", "32k", str(sil)], check=True)
    r = run([HERE / "transcribe.py", sil, "--out", tmp / "q"], stdin=subprocess.DEVNULL)
    # 키가 없으면 선점검이 무음 판정보다 먼저 막는다. 그것도 "막았다"가 맞다 —
    # 막지 못한 것과 구분하지 않으면 새로 설치한 사람이 가짜 실패를 본다.
    blocked_earlier = "준비되지 않은" in r.stdout or "키: 없음" in r.stdout
    check("무음 파일을 사전에 차단",
          r.returncode == 3 or "너무 조용" in r.stdout or blocked_earlier,
          f"rc={r.returncode}")
    if blocked_earlier:
        print("       (키 미설정 — 선점검이 먼저 막았다. 키를 넣고 다시 돌리면 무음 판정까지 간다)")

    if a.live:
        print("\n[7] 실호출 (Groq)")
        r = run([HERE / "transcribe.py", sample, "--out", tmp / "live", "--yes-external"])
        check("전 경로 성공", r.returncode == 0, r.stdout.strip()[-200:])
        raw = tmp / "live" / "raw"
        if raw.is_dir():
            txt = next(raw.glob("*.txt"), None)
            check("전사 결과 생성", txt is not None and txt.stat().st_size > 0)
            check("실행 기록 생성", (raw / "_실행기록.json").is_file())
            check("전사 메타 생성", (raw / "_전사메타.json").is_file())

    shutil.rmtree(tmp, ignore_errors=True)
    bad = [n for n, ok, _ in results if not ok]
    print(f"\n{'='*52}\n  통과 {len(results)-len(bad)} / {len(results)}")
    if bad:
        print("  실패:", ", ".join(bad))
    sys.exit(1 if bad else 0)


if __name__ == "__main__":
    main()
