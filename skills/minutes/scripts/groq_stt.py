#!/usr/bin/env python3
"""groq_stt.py: prepare_audio.py 가 만든 조각을 Groq 로 보내고 하나로 합친다.

  python3 prepare_audio.py "회의.mp4" --out 작업폴더
  python3 groq_stt.py 작업폴더

결과는 로컬 전사와 같은 모양으로 `작업폴더/raw/` 에 남는다.
교정 단계는 로컬이든 클라우드든 동일하게 이어진다.

한도 (무료 티어)
  요청당 25MB · 분당 20회 · 시간당 오디오 7,200초 · 하루 28,800초
이 스크립트는 시간당·하루 한도를 자체적으로 세고, 넘으면 기다린다.
"""
import argparse
import json
import os
import sys
import time

API = "https://api.groq.com/openai/v1/audio/transcriptions"
MODEL = "whisper-large-v3"
KEY_PATHS = [
    os.path.expanduser("~/.config/{{WORKSPACE_SLUG}}/groq.json"),
    os.path.expanduser("~/.groq.json"),
]
HARD_CUT_OVERLAP_S = 4.0   # prepare_audio.py 와 같은 값
CKPT_DIR_NAME = "_조각결과"   # 조각별 응답을 먼저 여기에 확정한다
RPM = 20              # 분당 요청
HOUR_AUDIO_S = 7200   # 시간당 오디오 초
DAY_AUDIO_S = 28800   # 하루 오디오 초
STATE = os.path.expanduser("~/.config/{{WORKSPACE_SLUG}}/groq_usage.json")


def _atomic_json(path, obj):
    """임시 파일에 쓰고 교체한다. 강제 종료 시 잘린 JSON 이 남지 않게."""
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


def load_key():
    env = os.environ.get("GROQ_API_KEY")
    if env:
        return env
    for p in KEY_PATHS:
        if os.path.isfile(p):
            try:
                return json.load(open(p, encoding="utf-8"))["api_key"]
            except (KeyError, ValueError):
                sys.exit(f"[!!] {p} 에 api_key 가 없다.")
    sys.exit("[!!] Groq 키가 없다. ~/.config/{{WORKSPACE_SLUG}}/groq.json 에 "
             '{"api_key": "gsk_..."} 형태로 두거나 GROQ_API_KEY 를 설정하라.')


def load_usage():
    if os.path.isfile(STATE):
        try:
            return json.load(open(STATE, encoding="utf-8"))
        except ValueError:
            pass
    return {"events": []}


def save_usage(u):
    os.makedirs(os.path.dirname(STATE), exist_ok=True)
    u["events"] = [e for e in u["events"] if time.time() - e["t"] < 86400]
    json.dump(u, open(STATE, "w", encoding="utf-8"), ensure_ascii=False)


def wait_for_budget(usage, need_s):
    """시간당·하루 한도를 넘지 않도록 기다린다."""
    while True:
        now = time.time()
        hour = sum(e["s"] for e in usage["events"] if now - e["t"] < 3600)
        day = sum(e["s"] for e in usage["events"] if now - e["t"] < 86400)
        if day + need_s > DAY_AUDIO_S:
            sys.exit(f"[!!] 하루 한도 초과. 오늘 {day/3600:.1f}시간 사용. 내일 이어서 하라.")
        if hour + need_s <= HOUR_AUDIO_S:
            return
        oldest = min(e["t"] for e in usage["events"] if now - e["t"] < 3600)
        sleep = int(3600 - (now - oldest)) + 5
        print(f"[*] 시간당 한도({HOUR_AUDIO_S/3600:.0f}시간)에 걸렸다. "
              f"{sleep//60}분 {sleep%60}초 대기...", flush=True)
        time.sleep(sleep)


def _retry_after(v, default=30):
    """Retry-After 는 초(정수·소수)일 수도 HTTP 날짜일 수도 있다."""
    if not v:
        return default
    try:
        return max(1, int(float(v)))
    except ValueError:
        pass
    try:
        from email.utils import parsedate_to_datetime
        import datetime as _dt
        dt = parsedate_to_datetime(v)
        delta = (dt - _dt.datetime.now(dt.tzinfo)).total_seconds()
        return max(1, int(delta))
    except Exception:
        return default


def send(path, key, language, retries=5):
    import requests
    last = ""
    for attempt in range(retries):
        try:
            with open(path, "rb") as fh:
                r = requests.post(
                    API,
                    headers={"Authorization": f"Bearer {key}"},
                    files={"file": (os.path.basename(path), fh, "audio/ogg")},
                    data={"model": MODEL, "language": language,
                          "response_format": "verbose_json",
                          "temperature": "0"},
                    timeout=600)
        except requests.exceptions.RequestException as e:
            last = f"네트워크 오류: {type(e).__name__}"
            wait = min(60, 4 * (attempt + 1))
            print(f"[*] {last}. {wait}초 후 재시도 ({attempt+1}/{retries})", flush=True)
            time.sleep(wait)
            continue
        if r.status_code == 200:
            try:
                return r.json()
            except ValueError:
                last = "응답이 JSON 이 아니다"
                time.sleep(4)
                continue
        if r.status_code == 429:
            wait = _retry_after(r.headers.get("retry-after"))
            print(f"[*] 429 한도. {wait}초 대기 후 재시도 "
                  f"({attempt+1}/{retries})", flush=True)
            time.sleep(wait)
            continue
        if 500 <= r.status_code < 600:
            wait = min(60, 4 * (attempt + 1))
            last = f"서버 오류 {r.status_code}"
            print(f"[*] {last}. {wait}초 후 재시도 ({attempt+1}/{retries})", flush=True)
            time.sleep(wait)
            continue
        sys.exit(f"[!!] Groq 오류 {r.status_code}: {r.text[:400]}")
    sys.exit(f"[!!] 재시도 한도 초과. 마지막 상태: {last}\n"
             f"    이미 전사된 조각은 {CKPT_DIR_NAME}/ 에 남아 있다. 다시 실행하면 이어서 한다.")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("workdir", help="prepare_audio.py 의 --out 과 같은 폴더")
    ap.add_argument("--lang", default="ko")
    a = ap.parse_args()

    plan_path = os.path.join(a.workdir, "_전송용", "_전송계획.json")
    if not os.path.isfile(plan_path):
        sys.exit(f"[!!] 전송 계획이 없다: {plan_path}\n    먼저 prepare_audio.py 를 돌려라.")
    plan = json.load(open(plan_path, encoding="utf-8"))
    key = load_key()
    usage = load_usage()

    chunks = plan["chunks"]
    print(f"[*] 조각 {len(chunks)}개 · 총 {plan['total_audio_s']/60:.1f}분 전송")

    ckpt = os.path.join(a.workdir, CKPT_DIR_NAME)
    os.makedirs(ckpt, exist_ok=True)

    segments, last_req, empty_chunks = [], 0.0, []
    for i, c in enumerate(chunks, 1):
        cf = os.path.join(ckpt, f"{i:02d}.json")
        if os.path.isfile(cf):        # 이미 한 조각은 건너뛴다 (재개)
            try:
                res = json.load(open(cf, encoding="utf-8"))
                print(f"[*] {i}/{len(chunks)} 이미 완료됨. 건너뛴다.", flush=True)
            except ValueError:
                os.remove(cf); res = None
        else:
            res = None
        if res is None:
            wait_for_budget(usage, c["dur_s"])
            gap = 60.0 / RPM - (time.time() - last_req)
            if gap > 0:
                time.sleep(gap)
            print(f"[*] {i}/{len(chunks)} 전송 중 "
                  f"({os.path.getsize(c['file'])/1e6:.2f}MB, {c['dur_s']/60:.1f}분)...",
                  flush=True)
            t0 = time.time()
            res = send(c["file"], key, a.lang)
            last_req = time.time()
            usage["events"].append({"t": last_req, "s": c["dur_s"]})
            save_usage(usage)
            _atomic_json(cf, res)     # 다음 조각으로 가기 전에 디스크에 확정
            print(f"    {time.time()-t0:.1f}초 · 세그먼트 "
                  f"{len(res.get('segments') or [])}개", flush=True)

        off = c["offset_s"]
        got = res.get("segments") or []
        if not got:
            empty_chunks.append(i)
        for sg in got:
            segments.append({"start": round(sg["start"] + off, 2),
                             "end": round(sg["end"] + off, 2),
                             "text": sg["text"].strip()})

    # 겹쳐 보낸 구간에서 생긴 중복만 제거한다.
    # 회의에서는 "네." 같은 짧은 맞장구가 연속으로 실제 반복되므로,
    # 겹침이 실제로 있는 구간(강제 분할한 조각의 앞머리)에서만 지운다.
    overlap_zones = []
    for c in chunks:
        if c.get("forced_cut"):
            overlap_zones.append((c["offset_s"], c["offset_s"] + HARD_CUT_OVERLAP_S + 1.0))

    def in_overlap(t):
        return any(a <= t <= b for a, b in overlap_zones)

    segments.sort(key=lambda s: s["start"])
    merged = []
    dropped = 0
    for s in segments:
        if (merged and in_overlap(s["start"]) and s["text"] == merged[-1]["text"]
                and s["start"] < merged[-1]["end"] + 1.0):
            dropped += 1
            continue
        merged.append(s)
    if dropped:
        print(f"[*] 겹침 구간 중복 {dropped}건 제거", flush=True)

    raw = os.path.join(a.workdir, "raw")
    os.makedirs(raw, exist_ok=True)
    base = os.path.splitext(os.path.basename(plan["source"]))[0]
    full = " ".join(s["text"] for s in merged).strip()

    with open(os.path.join(raw, f"{base}.txt"), "w", encoding="utf-8") as f:
        f.write(full + "\n")
    with open(os.path.join(raw, f"{base}.segments.txt"), "w", encoding="utf-8") as f:
        for s in merged:
            f.write(f"[{s['start']:8.2f} - {s['end']:8.2f}] {s['text']}\n")
    json.dump({"source": plan["source"], "engine": f"groq/{MODEL}",
               "duration_s": plan["duration_s"], "segments": merged, "text": full},
              open(os.path.join(raw, f"{base}.json"), "w", encoding="utf-8"),
              ensure_ascii=False, indent=1)

    # 사후 검증. 조용한 실패를 성공으로 넘기지 않는다.
    warn = []
    dur = plan.get("duration_s") or 0
    cov = (merged[-1]["end"] / dur * 100) if (merged and dur) else 0.0
    if not merged:
        warn.append("전사 결과가 비었다. 녹음에 음성이 없거나 전사가 실패했다")
    if empty_chunks:
        warn.append(f"조각 {empty_chunks} 의 전사가 비었다. 그 구간은 회의록에 없다")
    if dur and cov < 80:
        warn.append(f"커버리지 {cov:.0f}%. 녹음 길이에 비해 전사된 구간이 적다")
    if dur and len(full) < dur / 60 * 50:
        warn.append(f"분당 {len(full)/(dur/60):.0f}자. 발언이 거의 담기지 않았을 수 있다")

    meta = {"source": plan["source"], "engine": f"groq/{MODEL}",
            "duration_s": dur, "coverage_pct": round(cov, 1),
            "chunks": len(chunks), "empty_chunks": empty_chunks,
            "segments": len(merged), "chars": len(full), "warnings": warn}
    _atomic_json(os.path.join(raw, "_전사메타.json"), meta)

    now = time.time()
    hour = sum(e["s"] for e in usage["events"] if now - e["t"] < 3600)
    day = sum(e["s"] for e in usage["events"] if now - e["t"] < 86400)
    print(f"\n[OK] 세그먼트 {len(merged)}개 · {len(full)}자 · 커버리지 {cov:.0f}%")
    print(f"[OK] {raw}")
    print(f"[i] 사용량: 최근 1시간 {hour/60:.0f}분 / 120분 · "
          f"오늘 {day/3600:.1f} / 8시간")

    if warn:
        print("\n[!!] 전사 품질 경고")
        for w in warn:
            print(f"     - {w}")
        print("     이 상태로 회의록을 확정하면 빠진 발언이 있는 채로 정본이 된다.")
        print("     원인을 확인하기 전에는 status: final 로 올리지 마라.")
        sys.exit(2)

    print("[i] 전사는 끝났다. 다음은 SSOT 대조 교정이다. 원문을 고치지 말고 그대로 둬라.")


if __name__ == "__main__":
    main()
