#!/usr/bin/env python3
"""groq_setup.py: Groq 키를 저장하고 실제로 되는지 확인한다.

  python3 groq_setup.py gsk_xxxxxxxx
  python3 groq_setup.py --check          (이미 저장된 키 확인만)

키는 본인 계정으로 받는다. 무료 한도가 계정당이라 남의 키를 쓰면 한도를 나눠 쓴다.
"""
import argparse
import json
import os
import stat
import subprocess
import sys
import tempfile

CFG = os.path.expanduser("~/.config/{{WORKSPACE_SLUG}}/groq.json")
API = "https://api.groq.com/openai/v1/audio/transcriptions"
MODEL = "whisper-large-v3"


def save(key):
    if not key.startswith("gsk_"):
        sys.exit("[!!] Groq 키는 gsk_ 로 시작한다. 잘못 붙여넣은 것 같다.")
    os.makedirs(os.path.dirname(CFG), exist_ok=True)
    with open(CFG, "w", encoding="utf-8") as f:
        json.dump({"api_key": key}, f)
    os.chmod(CFG, stat.S_IRUSR | stat.S_IWUSR)   # 0600, 다른 사용자가 못 읽게
    print(f"[OK] 저장: {CFG} (권한 0600)")


def load():
    if os.environ.get("GROQ_API_KEY"):
        return os.environ["GROQ_API_KEY"]
    if not os.path.isfile(CFG):
        sys.exit(f"[!!] 키가 없다: {CFG}\n    python3 groq_setup.py gsk_... 로 저장하라.")
    return json.load(open(CFG, encoding="utf-8"))["api_key"]


def check(key):
    """1초짜리 무음을 만들어 실제로 한 번 호출해 본다."""
    import requests
    if not __import__("shutil").which("ffmpeg"):
        sys.exit("[!!] ffmpeg 가 없다. macOS: brew install ffmpeg / Windows: winget install ffmpeg")
    tmp = os.path.join(tempfile.gettempdir(), "_groq_ping.ogg")
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi",
                    "-i", "anullsrc=r=16000:cl=mono", "-t", "1",
                    "-c:a", "libopus", "-b:a", "32k", tmp], check=True)
    try:
        with open(tmp, "rb") as fh:
            r = requests.post(API, headers={"Authorization": f"Bearer {key}"},
                              files={"file": ("ping.ogg", fh, "audio/ogg")},
                              data={"model": MODEL, "language": "ko"}, timeout=60)
    finally:
        os.path.isfile(tmp) and os.remove(tmp)

    if r.status_code == 200:
        print("[OK] 키가 동작한다.")
        for h, label in (("x-ratelimit-remaining-audio-seconds", "남은 오디오(초)"),
                         ("x-ratelimit-limit-audio-seconds", "한도(초)"),
                         ("x-ratelimit-remaining-requests", "남은 요청")):
            if h in r.headers:
                print(f"     {label}: {r.headers[h]}")
        return 0
    if r.status_code == 401:
        sys.exit("[!!] 키가 거부됐다(401). 키를 다시 확인하라.")
    sys.exit(f"[!!] 오류 {r.status_code}: {r.text[:300]}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("key", nargs="?", help="gsk_ 로 시작하는 Groq 키")
    ap.add_argument("--check", action="store_true", help="저장된 키만 확인")
    a = ap.parse_args()
    if a.key:
        save(a.key)
    elif not a.check:
        ap.error("키를 주거나 --check 를 쓰라.")
    sys.exit(check(load()))


if __name__ == "__main__":
    main()
