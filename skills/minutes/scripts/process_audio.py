#!/usr/bin/env python3
"""local-whisper: faster-whisper(CTranslate2) 기반 로컬 STT → 회의록 생성.

전부 로컬에서 동작합니다. (API 키·외부 전송·과금 없음, MIT 라이선스)

Usage:
    python process_audio.py <audio_path> [options]

Options:
    --out  <dir>     회의록/원문 저장 기준 폴더. (기본: 오디오 파일 폴더)
                     원문(txt/json/segments)은 <out>/raw 에, 회의록 md 는 <out> 에 저장.
    --raw  <dir>     원문 저장 폴더를 직접 지정. (지정 시 --out 의 raw 대신 이 경로 사용)
    --model <size>   모델 크기. 기본: large-v3 (한국어 정확도 우선)
    --device <dev>   cpu | cuda | auto (기본: auto)
    --compute <t>    연산 타입. 기본: int8 (CPU에서 빠르고 메모리 절약)
    --lang <code>    언어 코드. 기본: ko
    --no-note        STT 만 수행하고 회의록 md 는 생성하지 않음.

예:
    python process_audio.py "회의.m4a" --out "/path/to/Project/Meeting"
"""
import argparse
import json
import os
import sys
from datetime import datetime


def transcribe(audio_path, raw_dir, model_size, device, compute_type, language):
    from faster_whisper import WhisperModel

    print(f"[*] 모델 로딩: {model_size} (device={device}, compute={compute_type})", flush=True)
    model = WhisperModel(model_size, device=device, compute_type=compute_type)

    print(f"[*] STT 시작: {audio_path}", flush=True)
    segments, info = model.transcribe(
        audio_path,
        language=language,
        beam_size=5,
        vad_filter=True,
        vad_parameters={"min_silence_duration_ms": 500},
    )
    print(f"[*] 감지 언어={info.language} (p={info.language_probability:.2f}), "
          f"길이={info.duration:.1f}s", flush=True)

    seg_list = []
    texts = []
    for s in segments:
        text = s.text.strip()
        seg_list.append({"start": round(s.start, 2), "end": round(s.end, 2), "text": text})
        texts.append(text)
        print(f"[{s.start:7.1f} -> {s.end:7.1f}] {text}", flush=True)

    full = " ".join(texts).strip()

    base = os.path.splitext(os.path.basename(audio_path))[0]
    os.makedirs(raw_dir, exist_ok=True)
    json_path = os.path.join(raw_dir, f"{base}.json")
    txt_path = os.path.join(raw_dir, f"{base}.txt")
    seg_path = os.path.join(raw_dir, f"{base}.segments.txt")

    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(
            {
                "language": info.language,
                "duration": info.duration,
                "model": model_size,
                "text": full,
                "segments": seg_list,
            },
            f,
            ensure_ascii=False,
            indent=2,
        )
    with open(txt_path, "w", encoding="utf-8") as f:
        f.write(full + "\n")
    with open(seg_path, "w", encoding="utf-8") as f:
        for s in seg_list:
            f.write(f"[{s['start']:>8.2f} - {s['end']:>8.2f}] {s['text']}\n")

    print(f"[*] 원문 저장: {txt_path}", flush=True)
    print(f"[*] 타임스탬프 저장: {seg_path}", flush=True)
    print(f"[*] JSON 저장: {json_path}", flush=True)
    return full, txt_path


def generate_meeting_note(raw_text, template_path, output_path):
    with open(template_path, "r", encoding="utf-8") as f:
        template = f.read()

    now = datetime.now()
    content = template.replace("{{date}}", now.strftime("%Y-%m-%d"))
    content = content.replace("{{time}}", now.strftime("%H:%M"))
    content = content.replace("{{raw_text}}", raw_text)
    content = content.replace("{{location}}", "TBD")
    content = content.replace("{{participants}}", "TBD")
    content = content.replace("{{subject}}", "회의 기록")
    content = content.replace("{{summary}}", "내용을 요약해 주세요. (LLM 후처리)")

    with open(output_path, "w", encoding="utf-8") as f:
        f.write(content)
    print(f"[*] 회의록 생성: {output_path}", flush=True)


def main():
    parser = argparse.ArgumentParser(description="faster-whisper STT → 회의록")
    parser.add_argument("audio_path")
    parser.add_argument("--out", default=None)
    parser.add_argument("--raw", default=None)
    parser.add_argument("--model", default="large-v3")
    parser.add_argument("--device", default="auto")
    parser.add_argument("--compute", default="int8")
    parser.add_argument("--lang", default="ko")
    parser.add_argument("--no-note", action="store_true")
    args = parser.parse_args()

    if not os.path.isfile(args.audio_path):
        print(f"[!] 오디오 파일 없음: {args.audio_path}")
        sys.exit(1)

    out_dir = args.out or os.path.dirname(os.path.abspath(args.audio_path))
    raw_dir = args.raw or os.path.join(out_dir, "raw")

    raw_text, _ = transcribe(
        args.audio_path, raw_dir, args.model, args.device, args.compute, args.lang
    )
    if not raw_text:
        print("[!] STT 결과가 비어 있음.")
        sys.exit(1)

    if not args.no_note:
        script_dir = os.path.dirname(os.path.abspath(__file__))
        template_path = os.path.join(script_dir, "..", "assets", "meeting_template.md")
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        note_path = os.path.join(out_dir, f"{timestamp}_Meeting_Notes.md")
        generate_meeting_note(raw_text, template_path, note_path)

    print("[OK] 완료")


if __name__ == "__main__":
    main()
