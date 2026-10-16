#!/usr/bin/env python3
"""prepare_audio.py: 영상/음성을 Groq 전송 규격으로 전처리한다.

하는 일
  1. 영상이면 음성만 추출한다 (ffprobe 로 판별)
  2. 16kHz 모노 Opus 로 변환한다 (Groq 가 어차피 16kHz 모노로 내린다)
  3. 25MB 를 넘으면 무음 지점에서 쪼갠다 (단어 중간을 자르지 않는다)
  4. 조각 목록과 각 조각의 시작 오프셋을 JSON 으로 남긴다

전송은 하지 않는다. 전송은 groq_stt.py 가 이 결과를 읽어서 한다.

  python3 prepare_audio.py "회의.mp4" --out "작업폴더"
"""
import argparse
import json
import math
import os
import re
import shutil
import subprocess
import sys

# Groq 무료 티어: 요청당 25MB. 안전선을 두고 24MB 로 자른다.
MAX_BYTES = 24 * 1024 * 1024
# 무음 탐색 허용 범위. 목표 경계에서 이만큼 안에 무음이 있으면 거기서 자른다.
SILENCE_WINDOW_S = 45.0
# 무음으로 칠 기준
SILENCE_NOISE_DB = -32
SILENCE_MIN_S = 0.35
# 무음을 못 찾아 강제로 자를 때 겹치는 길이 (경계 단어 유실 방지)
HARD_CUT_OVERLAP_S = 4.0
# 재인코딩 시 쓸 비트레이트 (main 에서 채운다)
BITRATE = [32]


def need(cmd):
    if not shutil.which(cmd):
        sys.exit(f"[!!] {cmd} 없음. macOS: brew install ffmpeg / Windows: winget install ffmpeg")


def probe(path):
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_streams", "-show_format", "-of", "json", path],
        capture_output=True, text=True)
    if out.returncode != 0:
        sys.exit(f"[!!] 파일을 읽을 수 없다: {path}\n{out.stderr.strip()}")
    d = json.loads(out.stdout)
    streams = d.get("streams", [])
    has_video = any(s.get("codec_type") == "video"
                    and s.get("disposition", {}).get("attached_pic", 0) == 0
                    for s in streams)
    has_audio = any(s.get("codec_type") == "audio" for s in streams)
    dur = float(d.get("format", {}).get("duration", 0) or 0)
    n_audio = sum(1 for x in streams if x.get("codec_type") == "audio")
    return has_video, has_audio, dur, n_audio


def to_opus(src, dst, bitrate, astream=0):
    """음성만 뽑아 16kHz 모노 Opus 로 만든다.

    astream 은 몇 번째 오디오 스트림을 쓸지다. OBS 녹화처럼 트랙이 여럿이면
    암묵 선택에 맡기지 않고 명시한다 (0 = 첫 트랙 = OBS 프로파일의 혼합 트랙).
    """
    cmd = ["ffmpeg", "-v", "error", "-y", "-i", src,
           "-map", f"0:a:{astream}", "-vn", "-ar", "16000", "-ac", "1",
           "-c:a", "libopus", "-b:a", f"{bitrate}k", dst]
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        sys.exit(f"[!!] 변환 실패\n{r.stderr.strip()}")


def find_silences(path):
    """무음 구간 [(start, end)] 를 찾는다. ffmpeg 가 stderr 로 뱉는다."""
    r = subprocess.run(
        ["ffmpeg", "-v", "info", "-i", path,
         "-af", f"silencedetect=noise={SILENCE_NOISE_DB}dB:d={SILENCE_MIN_S}",
         "-f", "null", "-"],
        capture_output=True, text=True)
    if r.returncode != 0:
        print("[!] 무음 탐색 실패. 균등 분할로 진행한다.", flush=True)
        return []
    starts = [float(m) for m in re.findall(r"silence_start: ([0-9.]+)", r.stderr)]
    ends = [float(m) for m in re.findall(r"silence_end: ([0-9.]+)", r.stderr)]
    # 파일 끝까지 이어지는 무음은 silence_end 가 없다. 짝이 안 맞으면 남는 start 를 버린다.
    if len(starts) != len(ends):
        print(f"[!] 무음 구간 짝 불일치 (start {len(starts)} / end {len(ends)}). "
              f"짝 맞는 것만 쓴다.", flush=True)
    return list(zip(starts, ends))


def pick_cut_points(duration, n_chunks, silences):
    """경계마다 가장 가까운 무음의 중간점을 고른다. 없으면 목표 지점을 그대로 쓴다."""
    points, forced = [], []
    for i in range(1, n_chunks):
        target = duration * i / n_chunks
        best, best_d = None, None
        for s, e in silences:
            mid = (s + e) / 2
            d = abs(mid - target)
            if d <= SILENCE_WINDOW_S and (best_d is None or d < best_d):
                best, best_d = mid, d
        if best is None:
            points.append(target)
            forced.append(True)
        else:
            points.append(best)
            forced.append(False)
    return points, forced


def cut(src, dst, start, end):
    cmd = ["ffmpeg", "-v", "error", "-y", "-ss", f"{start:.3f}"]
    if end is not None:
        cmd += ["-to", f"{end:.3f}"]
    cmd += ["-i", src, "-c", "copy", dst]
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:  # copy 가 안 되면 재인코딩
        cmd = ["ffmpeg", "-v", "error", "-y", "-ss", f"{start:.3f}"]
        if end is not None:
            cmd += ["-to", f"{end:.3f}"]
        cmd += ["-i", src, "-c:a", "libopus", "-b:a", f"{BITRATE[0]}k", dst]
        r = subprocess.run(cmd, capture_output=True, text=True)
        if r.returncode != 0:
            sys.exit(f"[!!] 분할 실패\n{r.stderr.strip()}")


def _split_once(whole, work, base, dur, sil, n, chunks):
    """n조각으로 나눠 chunks 를 채운다. 실제 조각 파일을 만든다."""
    chunks.clear()
    cuts, forced = pick_cut_points(dur, n, sil)
    bounds = [0.0] + cuts + [dur]
    for i in range(n):
        st, en = bounds[i], bounds[i + 1]
        if i > 0 and forced[i - 1]:
            st = max(0.0, st - HARD_CUT_OVERLAP_S)
        dst = os.path.join(work, f"{base}_{i+1:02d}.ogg")
        cut(whole, dst, st, en if i < n - 1 else None)
        chunks.append({"file": dst, "offset_s": round(st, 2),
                       "dur_s": round(en - st, 2),
                       "forced_cut": bool(i > 0 and forced[i - 1])})
    return chunks


def _split_fitting(whole, work, base, dur, sil, n, max_bytes, chunks):
    """모든 조각이 한도 안에 들어갈 때까지 조각 수를 늘린다.

    Opus 는 가변 비트레이트라 균등 분할해도 특정 조각이 한도를 넘을 수 있다.
    만들고 나서 실제 바이트를 재고, 넘으면 조각 수를 늘려 다시 나눈다.
    """
    for attempt in range(6):
        _split_once(whole, work, base, dur, sil, n, chunks)
        over = [(i, os.path.getsize(c["file"])) for i, c in enumerate(chunks, 1)
                if os.path.getsize(c["file"]) > max_bytes]
        if not over:
            break
        biggest = max(sz for _, sz in over)
        unit = f"{biggest/1e6:.2f}MB" if biggest >= 1e5 else f"{biggest/1000:.0f}KB"
        print(f"[!] {len(over)}개 조각이 한도를 넘는다 (최대 {unit}). "
              f"조각 수를 {n} >> {n+1} 로 늘린다.", flush=True)
        n += 1
    else:
        sys.exit(f"[!!] 조각을 한도({max_bytes/1e6:.2f}MB) 안으로 줄이지 못했다.\n"
                 f"    조각을 아무리 잘게 나눠도 컨테이너 오버헤드가 남는다.\n"
                 f"    --max-mb 를 올리거나 --bitrate 를 낮춰라.")
    for i, c in enumerate(chunks, 1):
        st, en = c["offset_s"], c["offset_s"] + c["dur_s"]
        mark = " (무음 없어 강제 분할 · 겹침 적용)" if c["forced_cut"] else ""
        print(f"    {i:02d}. {st/60:6.1f}~{en/60:6.1f}분  "
              f"{os.path.getsize(c['file'])/1e6:5.2f}MB{mark}")
    return n


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("input")
    ap.add_argument("--out", required=True, help="결과를 둘 폴더")
    ap.add_argument("--bitrate", type=int, default=32, help="Opus kbps (기본 32)")
    ap.add_argument("--max-mb", type=float, default=24.0,
                    help="조각당 최대 크기(십진 MB). Groq 무료 한도는 25MB")
    ap.add_argument("--astream", type=int, default=0,
                    help="쓸 오디오 트랙. OBS 녹화는 0=혼합 1=시스템 2=마이크")
    a = ap.parse_args()

    need("ffmpeg"); need("ffprobe")
    if not os.path.isfile(a.input):
        sys.exit(f"[!!] 파일 없음: {a.input}")
    if a.max_mb <= 0:
        sys.exit("[!!] --max-mb 는 0보다 커야 한다.")
    if not (6 <= a.bitrate <= 320):
        sys.exit("[!!] --bitrate 는 6에서 320 사이여야 한다.")

    BITRATE[0] = a.bitrate
    max_bytes = int(a.max_mb * 1000 * 1000)   # Groq 한도는 십진 MB 로 본다 (보수적)
    work = os.path.join(a.out, "_전송용")
    os.makedirs(work, exist_ok=True)

    has_video, has_audio, dur, n_audio = probe(a.input)
    if not has_audio:
        sys.exit("[!!] 음성 트랙이 없다.")
    if dur <= 0:
        sys.exit("[!!] 길이를 읽지 못했다. 파일이 손상됐을 수 있다.")
    print(f"[*] 입력: {'영상+음성' if has_video else '음성'} · {dur/60:.1f}분"
          + (f" · 오디오 트랙 {n_audio}개" if n_audio > 1 else ""))
    if n_audio > 1:
        print(f"[*] 트랙이 여럿이다. 첫 트랙(혼합)을 쓴다. "
              f"다른 트랙은 --astream 으로 고른다.")
    if has_video:
        print("[*] 영상 트랙은 버리고 음성만 추출한다.")

    base = re.sub(r"[^\w가-힣._-]+", "_", os.path.splitext(os.path.basename(a.input))[0])
    whole = os.path.join(work, f"{base}.ogg")
    print(f"[*] 16kHz 모노 Opus {a.bitrate}kbps 로 변환 중...")
    to_opus(a.input, whole, a.bitrate, a.astream)
    size = os.path.getsize(whole)
    # 변환 결과를 다시 재어 원본 길이와 크게 다르면 멈춘다
    _, _, dur_out, _ = probe(whole)
    if dur_out <= 0:
        sys.exit("[!!] 변환본의 길이를 읽지 못했다.")
    if abs(dur_out - dur) > max(5.0, dur * 0.02):
        sys.exit(f"[!!] 변환본 길이가 원본과 다르다 (원본 {dur:.1f}s / 변환 {dur_out:.1f}s). "
                 f"입력이 손상됐거나 트랙 선택이 잘못됐다.")
    dur = dur_out
    print(f"[*] 변환 완료: {size/1048576:.2f}MB · {dur/60:.1f}분")

    chunks = []
    if size <= max_bytes:
        chunks.append({"file": whole, "offset_s": 0.0, "dur_s": round(dur, 2), "forced_cut": False})
        print(f"[*] 25MB 안에 들어간다. 분할 없음.")
    else:
        n = math.ceil(size / max_bytes)
        print(f"[*] {size/1048576:.1f}MB > {a.max_mb}MB · {n}조각으로 나눈다.")
        print(f"[*] 무음 지점을 찾는 중...")
        sil = find_silences(whole)
        print(f"[*] 무음 {len(sil)}곳 발견")
        n = _split_fitting(whole, work, base, dur, sil, n, max_bytes, chunks)

    total_audio = sum(c["dur_s"] for c in chunks)
    plan = {"source": os.path.abspath(a.input), "duration_s": round(dur, 2),
            "had_video": has_video, "bitrate_kbps": a.bitrate,
            "chunks": chunks, "total_audio_s": round(total_audio, 2)}
    pf = os.path.join(work, "_전송계획.json")
    json.dump(plan, open(pf, "w", encoding="utf-8"), ensure_ascii=False, indent=1)

    print(f"\n[OK] 조각 {len(chunks)}개 · 총 {total_audio/60:.1f}분")
    print(f"[OK] 전송 계획: {pf}")
    # 무료 티어 한도 대비 안내
    if total_audio > 7200:
        print(f"[!] 시간당 한도(2시간)를 넘는다. 전송 중 대기가 생긴다.")
    if total_audio > 28800:
        print(f"[!!] 하루 한도(8시간)를 넘는다. 나눠서 처리해야 한다.")


if __name__ == "__main__":
    main()
