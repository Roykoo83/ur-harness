#!/usr/bin/env python3
"""transcribe.py: 회의 녹음을 전사한다. **이것 하나만 부르면 된다.**

  python3 transcribe.py "회의.mp4" --out "작업폴더"
  python3 transcribe.py "회의.mp4" --out "작업폴더" --offline   (인터넷 없을 때)
  python3 transcribe.py --doctor                               (환경 점검)

기본은 Groq 다. 녹음이 외부 서버로 전송된다. 실행 전에 그 사실을 알리고 승인을 받는다.
`--offline` 은 로컬 whisper 로 돈다. 외부로 나가지 않지만 녹음 길이의 약 2배가 걸린다.

맥과 윈도우에서 같은 명령으로 동작한다.
"""
import argparse
import hashlib
import json
import os
import platform
import shutil
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
IS_WIN = platform.system() == "Windows"


# ---------------------------------------------------------------- 환경

def py_exe():
    return sys.executable or ("py" if IS_WIN else "python3")


def have(cmd):
    return shutil.which(cmd) is not None


def install_hint(what):
    if what in ("ffmpeg", "ffprobe"):
        return ("winget install --id Gyan.FFmpeg -e" if IS_WIN
                else "brew install ffmpeg")
    return f"{py_exe()} -m pip install {what}"


def doctor(verbose=True):
    """설치 상태를 점검한다. 부족한 것과 고치는 명령을 함께 알려준다."""
    rows, ok = [], True
    rows.append(("OS", f"{platform.system()} {platform.release()}", True, ""))
    rows.append(("파이썬", platform.python_version(),
                 sys.version_info >= (3, 9), "3.9 이상이 필요하다"))
    for c in ("ffmpeg", "ffprobe"):
        g = have(c)
        rows.append((c, "있음" if g else "없음", g, install_hint(c)))
    for m in ("requests",):
        try:
            __import__(m); g = True
        except ImportError:
            g = False
        rows.append((m, "있음" if g else "없음", g, install_hint(m)))
    keyfile = Path.home() / ".config" / "{{WORKSPACE_SLUG}}" / "groq.json"
    g = keyfile.is_file() or bool(os.environ.get("GROQ_API_KEY"))
    rows.append(("Groq 키", "있음" if g else "없음", g,
                 f"{py_exe()} {HERE/'groq_setup.py'} gsk_..."))
    try:
        import faster_whisper  # noqa
        fw = True
    except ImportError:
        fw = False
    rows.append(("faster-whisper", "있음" if fw else "없음 (오프라인 경로용)",
                 True, install_hint("faster-whisper") + "  # --offline 쓸 때만"))

    if verbose:
        print("환경 점검\n")
        for name, val, good, hint in rows:
            mark = "OK " if good else "!! "
            print(f"  {mark}{name:<16}{val}")
            if not good and hint:
                print(f"       고치는 법: {hint}")
        print()
    ok = all(r[2] for r in rows)
    return ok, rows


# ---------------------------------------------------------------- 사전 검사

def preflight_audio(path):
    """전사 전에 녹음이 쓸 만한지 본다. 조용한 녹음을 미리 잡는다."""
    import array, math
    p = subprocess.run(["ffmpeg", "-v", "error", "-i", str(path),
                        "-vn", "-ar", "16000", "-ac", "1", "-f", "s16le", "-"],
                       capture_output=True)
    if p.returncode != 0 or not p.stdout:
        return None
    a = array.array("h"); a.frombytes(p.stdout[:len(p.stdout) // 2 * 2])
    if not len(a):
        return None
    SR = 16000
    dur = len(a) / SR
    # 창 크기는 30초. 다만 짧은 파일은 창이 하나도 안 나오므로 길이에 맞춰 줄인다.
    W = int(min(30, max(2, dur / 3)) * SR)
    tot = sum(x * x for x in a) / len(a)
    overall = 10 * math.log10(tot / (32768 ** 2) + 1e-12)
    win, good = 0, 0
    for i in range(0, len(a), W):
        ch = a[i:i + W]
        if len(ch) < W // 2:
            break
        s = sum(x * x for x in ch) / len(ch)
        win += 1
        if 10 * math.log10(s / (32768 ** 2) + 1e-12) > -35:
            good += 1
    return {"overall_db": round(overall, 1), "windows": win, "loud_windows": good,
            "loud_ratio": round(good / win, 2) if win else None,
            "window_s": round(W / SR, 1), "duration_s": round(dur, 1)}


def sha256(path, limit=64 * 1024 * 1024):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while True:
            b = f.read(1024 * 1024)
            if not b:
                break
            h.update(b)
            if f.tell() > limit:
                break
    return h.hexdigest()[:16]


# ---------------------------------------------------------------- 본체

def ask(prompt, flag_hint):
    """대화형이면 묻고, 아니면 거절한다.

    에이전트나 스크립트가 부르면 stdin 이 없어 input() 이 터진다.
    그때는 멈추고 어떤 옵션을 붙이면 되는지 알려준다.
    """
    if not sys.stdin or not sys.stdin.isatty():
        print(f"\n     대화형 입력을 받을 수 없다. 계속하려면 {flag_hint} 를 붙여 다시 실행하라.")
        return False
    try:
        return input(prompt).strip().lower() == "y"
    except (EOFError, KeyboardInterrupt):
        return False


def run(cmd):
    print(f"\n$ {' '.join(str(c) for c in cmd)}\n", flush=True)
    return subprocess.run(cmd).returncode


def main():
    ap = argparse.ArgumentParser(description="회의 녹음 전사 (맥·윈도우 공통)")
    ap.add_argument("input", nargs="?", help="녹음 또는 녹화 파일")
    ap.add_argument("--out", help="결과를 둘 폴더")
    ap.add_argument("--offline", action="store_true",
                    help="로컬 whisper 로 돈다. 외부 전송 없음. 녹음 길이의 약 2배")
    ap.add_argument("--yes-external", action="store_true",
                    help="외부 전송 확인을 건너뛴다 (자동화용)")
    ap.add_argument("--force-quiet", action="store_true",
                    help="녹음이 조용해도 그대로 진행한다")
    ap.add_argument("--astream", type=int, default=0,
                    help="쓸 오디오 트랙. OBS 녹화는 0=혼합 1=시스템 2=마이크")
    ap.add_argument("--doctor", action="store_true", help="환경만 점검하고 끝낸다")
    a = ap.parse_args()

    if a.doctor:
        ok, _ = doctor()
        print("모두 준비됨" if ok else "위의 '!!' 항목을 먼저 해결하라")
        sys.exit(0 if ok else 1)

    if not a.input or not a.out:
        ap.error("input 과 --out 이 필요하다. 환경만 보려면 --doctor")

    src = Path(a.input).expanduser()
    if not src.is_file():
        sys.exit(f"[!!] 파일 없음: {src}")
    out = Path(a.out).expanduser()
    out.mkdir(parents=True, exist_ok=True)

    ok, rows = doctor(verbose=False)
    need = [r for r in rows if not r[2]]
    if need:
        print("[!!] 준비되지 않은 것이 있다.\n")
        for name, val, _, hint in need:
            print(f"  - {name}: {val}\n    고치는 법: {hint}")
        sys.exit(1)

    # 사전 음성 검사
    print("[*] 녹음 상태를 확인하는 중...", flush=True)
    pf = preflight_audio(src)
    if pf:
        ratio = (f" · 유효 발화 구간 {pf['loud_windows']}/{pf['windows']}"
                 if pf["windows"] else "")
        print(f"[*] 길이 {pf['duration_s']/60:.1f}분 · 평균 음량 {pf['overall_db']}dB{ratio}")
        quiet = pf["overall_db"] < -34
        sparse = pf["loud_ratio"] is not None and pf["loud_ratio"] < 0.25
        if quiet or sparse:
            print("\n[!!] 녹음이 너무 조용하다. 전사해도 발언이 거의 안 나올 수 있다.")
            print("     맥 화면 기록은 마이크만 잡아 상대 발언이 빠진다. OBS 를 쓰라.")
            print("     (OBS_회의녹화.md 참고)")
            if not a.force_quiet and not ask("\n     그래도 진행하겠나? (y/N) ",
                                             "--force-quiet"):
                sys.exit(3)

    # 외부 전송 고지
    if not a.offline and not a.yes_external:
        print("\n" + "=" * 62)
        print("  이 녹음은 Groq(미국) 서버로 전송되어 전사된다.")
        print("  약관상 학습에 쓰지 않고 기본 보존도 하지 않지만, 외부로 나가는 것은 사실이다.")
        print("  외부 인사 발언 · 환자 정보 · 미공개 과제 내용이 있으면")
        print("  --offline 으로 로컬 전사를 쓰라 (느리지만 나가지 않는다).")
        print("=" * 62)
        if not ask("  전송해도 되겠나? (y/N) ", "--yes-external"):
            print("  중단했다. --offline 으로 다시 실행하면 외부로 나가지 않는다.")
            sys.exit(1)

    meta = {"source": str(src), "sha256_16": sha256(src),
            "os": f"{platform.system()} {platform.release()}",
            "python": platform.python_version(),
            "path": "offline/local-whisper" if a.offline else "groq",
            "preflight": pf}

    if a.offline:
        rc = run([py_exe(), str(HERE / "process_audio.py"), str(src),
                  "--out", str(out), "--no-note"])
    else:
        rc = run([py_exe(), str(HERE / "prepare_audio.py"), str(src),
                  "--out", str(out), "--astream", str(a.astream)])
        if rc == 0:
            rc = run([py_exe(), str(HERE / "groq_stt.py"), str(out)])

    (out / "raw").mkdir(parents=True, exist_ok=True)
    with open(out / "raw" / "_실행기록.json", "w", encoding="utf-8") as f:
        json.dump({**meta, "exit_code": rc}, f, ensure_ascii=False, indent=1)

    if rc == 2:
        print("\n[!] 전사는 끝났지만 품질 경고가 있다. 위 내용을 먼저 확인하라.")
    elif rc != 0:
        sys.exit(rc)
    print(f"\n[i] 실행 기록: {out/'raw'/'_실행기록.json'}")
    sys.exit(rc)


if __name__ == "__main__":
    main()
