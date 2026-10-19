#!/usr/bin/env python3
"""보안대장 재등재 — 파일명이 아니라 **본문을 읽어서** 등급을 매긴다.

운영자 지시(2026-09-22): "672건이 원장 미등재라는데 자료를 상세히 읽고 원장 등재해놔."
그리고 "S2·S3 권한은 아직 의미 없다. **일단 라벨만 만들어 놓은 뒤** 그 데이터를 기반으로
정리해야 한다." → 그래서 이 도구는 **권한을 만들지 않는다.** 라벨과 그 근거만 남긴다.

기존 `_시스템/도구/classify_security.py` 와 다른 점
  · 본문 읽기 범위가 md·txt·csv 2MB → **docx·pptx·xlsx·hwpx·pdf·hwp 까지** (5MB 이하)
  · `판정방식` 열 추가 — 본문/파일명/폴더/기본 중 무엇으로 정했는지. 나중에 정제할 때
    "파일명만 보고 매긴 것"을 골라낼 수 있다. 이것이 운영자 가 말한 "데이터 기반 정리"의 재료다
  · **인건비를 S3 키워드에서 뺐다** (운영자 결정 2026-09-07: "메일로 공유되는 인건비는 비밀이 아니다")
  · 마운트가 아니라 **API 목록**을 정본으로 본다. 본문은 마운트에서 읽는다(빠름), 없으면 API

안전
  · 기존 보안ID 를 보존한다 (NFC 정규화해 대조 — NFD 파일명 때문에 ID 가 갈린 전력)
  · 어떤 파일도 지우거나 옮기지 않는다. 대장 TSV 한 개만 쓴다 (기계 구역 · API)

사용: ledger_rebuild.py [--scope missing|text|all] [--apply]
"""
from __future__ import annotations
import argparse, csv, io, os, re, subprocess, sys, unicodedata, zipfile
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "shared"))

MOUNT = Path("{{MOUNT_ROOT}}/"
             "공유 드라이브/{{SSOT_DRIVE}}")
LEDGER_REL = "_시스템/보안/보안대장.tsv"
CAP_TEXT, CAP_BIN = 2 * 1024 * 1024, 5 * 1024 * 1024
HEAD = ["보안ID", "보안등급", "파일경로", "파일명", "근거", "판정방식"]

# ── 규칙 키워드 (보안등급_정의.md 의 사다리) ────────────────────────────
# 2026-09-07 운영자 결정으로 "인건비" 제외 — 인건비 계상·집행 논의는 비밀이 아니다.
S3_KW = ("주민등록", "주민번호", "계좌번호", "통장", "급여대장", "급여명세", "재무제표",
         "원천징수", "건강보험", "4대보험", "가입자명부", "주주명부", "카드번호",
         "비밀번호", "password", "credential", "크레덴셜", "이체확인", "납세증명")
S2_KW = ("대외비", "원가", "단가", "협상", "미수", "미확정", "내부논의", "내부전략", "confidential")
S0_KW = ("공고", "공고문", "홍보", "보도자료", "발표성과", "리플렛", "브로슈어")

RRN = re.compile(r"\b\d{6}[-–]\s?[1-4]\d{6}\b")
ACCT = re.compile(r"(계좌번호|예금주|통장사본|입금계좌|가상계좌)[^\n]{0,40}\d{6,}")
S2_BODY = re.compile(r"대외비|미수금|외상매출|CONFIDENTIAL", re.I)

TXT_EXT = {".md", ".txt", ".csv", ".tsv", ".json", ".jsonl", ".html", ".htm", ".log",
           ".py", ".js", ".mjs", ".ts", ".css", ".xml", ".yaml", ".yml", ".sh", ".ps1",
           ".sample", ".tmpl"}
OFC_EXT = {".docx", ".pptx", ".xlsx", ".hwpx"}
PDF_EXT = {".pdf"}
HWP_EXT = {".hwp"}


def nfc(s: str) -> str:
    return unicodedata.normalize("NFC", s)


# ── 본문 추출 ───────────────────────────────────────────────────────────
def extract_api(rel: str, ext: str, size: int) -> tuple[str, str]:
    """마운트가 못 읽은 파일을 API 로 가져와 읽는다.

    2026-09-22 1차 실행에서 마운트 읽기가 224건 실패했다(타임아웃 197 · 파일없음 27) — DriveFS 가
    아직 내려받지 않았거나 굳은 것이다. 그대로 두면 그 224건은 "파일명만 보고" 매겨진다.
    원장의 목적이 라벨의 신뢰도이므로, 실패한 것은 API 로 다시 읽는다.
    """
    if size > CAP_BIN:
        return "", "크기초과"
    tmp = Path.home() / "{{WORKSPACE_NAME}}/state/_ledger_tmp" / f"f{abs(hash(rel))%10**10}{ext}"
    tmp.parent.mkdir(parents=True, exist_ok=True)
    try:
        r = subprocess.run(["rclone", "copyto", f"SSOT_ro:{rel}", str(tmp), "-q"],
                           capture_output=True, text=True, timeout=180)
        if r.returncode != 0 or not tmp.exists():
            return "", "API실패"
        body, how = extract(tmp, ext, tmp.stat().st_size, _no_api=True)
        return body, ("본문(API)" if body else how)
    except (OSError, subprocess.TimeoutExpired):
        return "", "API실패"
    finally:
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass


def extract(path: Path, ext: str, size: int, _no_api: bool = False) -> tuple[str, str]:
    """(본문, 방식). 못 읽으면 ("", 사유)."""
    try:
        if ext in TXT_EXT:
            if size > CAP_TEXT:
                return "", "크기초과"
            return path.read_text(encoding="utf-8", errors="ignore")[:400_000], "본문"
        if size > CAP_BIN:
            return "", "크기초과"
        if ext in {".docx", ".pptx", ".xlsx", ".hwpx"}:
            # 전부 zip+xml 이다. 라이브러리 없이 태그를 걷어내면 충분하다.
            with zipfile.ZipFile(path) as z:
                want = [n for n in z.namelist()
                        if n.endswith(".xml") and not n.startswith("_rels")]
                buf = []
                for n in want[:300]:
                    try:
                        buf.append(z.read(n).decode("utf-8", "ignore"))
                    except Exception:                       # noqa: BLE001
                        pass
                raw = " ".join(buf)
            return re.sub(r"<[^>]+>", " ", raw)[:400_000], "본문"
        if ext in PDF_EXT:
            r = subprocess.run(["pdftotext", "-l", "30", "-q", str(path), "-"],
                               capture_output=True, text=True, timeout=90)
            return (r.stdout or "")[:400_000], ("본문" if r.stdout.strip() else "추출실패")
        if ext in HWP_EXT:
            import olefile                                   # noqa: PLC0415
            if not olefile.isOleFile(str(path)):
                return "", "HWP아님"
            ole = olefile.OleFileIO(str(path))
            out = []
            for st in ole.listdir():
                if st and st[0] in ("PrvText", "BodyText"):
                    try:
                        out.append(ole.openstream(st).read().decode("utf-16-le", "ignore"))
                    except Exception:                       # noqa: BLE001
                        pass
            ole.close()
            txt = re.sub(r"[^가-힣\w\s가-힣.,:/()-]", " ", " ".join(out))
            return txt[:400_000], ("본문" if txt.strip() else "추출실패")
    except (OSError, zipfile.BadZipFile, subprocess.TimeoutExpired, Exception) as e:  # noqa: BLE001
        return "", f"오류:{type(e).__name__}"
    return "", "이진"


# ── 등급 판정 (보안등급_정의.md 사다리 순서) ────────────────────────────
def classify(rel: str, body: str, how: str) -> tuple[str, str, str]:
    name, folder = os.path.basename(rel), os.path.dirname(rel)
    low = nfc(rel).lower()
    for k in S3_KW:                                          # ① 파일명·경로 S3
        if k.lower() in low:
            return "S3", "개인정보/금융/크레덴셜 키워드", "파일명"
    try:                                                     # ② sensitivity=high
        from rpbd.ssot.sensitivity import classify as sens   # noqa: PLC0415
        s = sens(name, folder)
        if s == "high":
            return "S3", "sensitivity=high", "파일명"
    except Exception:                                        # noqa: BLE001
        s = None
    if body:
        if RRN.search(body):                                 # ③ 본문 주민번호
            return "S3", "내용: 주민등록번호", "본문"
        if ACCT.search(body):
            return "S3", "내용: 계좌", "본문"
        # 등급을 *서술하는* 줄은 제외한다. `_INDEX.md` 의 `sensitivity_max: "S2 (대외비)"` 는
        # 그 폴더에 대외비가 있다는 메타데이터이고, 색인 파일 자신이 대외비인 것은 아니다.
        # 문서 자신의 frontmatter `sensitivity: 대외비` 는 자기 선언이므로 그대로 인정한다.
        scrub = re.sub(r"^\s*sensitivity_max\s*:.*$", "", body, flags=re.M)
        if S2_BODY.search(scrub):                            # ④ 본문 대외비
            return "S2", "내용: 대외비/미수", "본문"
    for k in S2_KW:                                          # ⑤ 파일명 S2
        if k.lower() in low:
            return "S2", "전략/원가/대외비 키워드", "파일명"
    if "40_커뮤니케이션/" not in nfc(rel):                     # ⑥ 파일명 공개성
        for k in S0_KW:
            if k in nfc(name):
                return "S0", "공개성 키워드", "파일명"
    if s == "medium":                                        # ⑦ sensitivity=medium
        return "S2", "sensitivity=medium", "파일명"
    return "S1", "기본(사내)", ("본문" if body else how or "파일명")   # ⑧


def load_live() -> dict[str, int]:
    r = subprocess.run(["rclone", "lsf", "-R", "--files-only", "--fast-list",
                        "--format", "sp", "--separator", "\t", "SSOT_ro:"],
                       capture_output=True, text=True, timeout=900)
    out = {}
    for line in r.stdout.splitlines():
        p = line.split("\t")
        if len(p) == 2:
            out[nfc(p[1])] = int(p[0])
    return out


def load_ledger() -> dict[str, dict]:
    r = subprocess.run(["rclone", "cat", f"SSOT_ro:{LEDGER_REL}"],
                       capture_output=True, text=True, timeout=300)
    out = {}
    for row in csv.DictReader(io.StringIO(r.stdout), delimiter="\t"):
        fp, fn = row["파일경로"], row["파일명"]
        key = nfc(fn if fp in (".", "", fn) else (fp if fp.endswith(fn) else f"{fp}/{fn}"))
        out[key] = row
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--scope", choices=["missing", "recheck", "text", "all"], default="missing")
    ap.add_argument("--apply", action="store_true")
    a = ap.parse_args()

    live, led = load_live(), load_ledger()
    missing = [p for p in live if p not in led and not os.path.basename(p).startswith(".")]
    # 규칙이 바뀐 뒤에는 그 규칙으로 매겨진 행만 다시 본다. 전량 재판정(1.6GB)을 피하는 길이다.
    RULE_CHANGED = {"sensitivity=high", "sensitivity=medium", "내용: 대외비/미수",
                    "개인정보/금융/크레덴셜 키워드"}
    if a.scope == "missing":
        targets = missing
    elif a.scope == "recheck":
        targets = missing + [p for p in live if p in led and led[p].get("근거") in RULE_CHANGED]
    elif a.scope == "text":
        targets = missing + [p for p in live if p in led and led[p].get("근거") == "기본(사내)"
                             and os.path.splitext(p)[1].lower() in TXT_EXT]
    else:
        targets = missing + [p for p in live if p in led and led[p].get("근거") == "기본(사내)"]
    targets = sorted(set(targets))

    nid = max([int(m.group(1)) for r in led.values()
               if (m := re.match(r"SEC-(\d+)$", r["보안ID"]))] or [0])
    rows, stat, changed, unread = {}, {"S0": 0, "S1": 0, "S2": 0, "S3": 0}, [], {}
    for k, r in led.items():                                  # 기존 보존
        if k in live:
            # 기존 행에는 판정방식 열이 없다. 근거에서 되살린다 — `내용:` 으로 시작하면
            # 그때도 본문을 읽고 매긴 것이다. 전부 "파일명"으로 채우면 신뢰도 열이 거짓말한다.
            if not r.get("판정방식"):
                r["판정방식"] = "본문" if r.get("근거", "").startswith("내용") else "파일명"
            rows[k] = r
    print(f"실제 {len(live)} · 원장 {len(led)} · 미등재 {len(missing)} · 이번 대상 {len(targets)}\n")

    for i, rel in enumerate(targets, 1):
        ext = os.path.splitext(rel)[1].lower()
        body, how = extract(MOUNT / rel, ext, live[rel])
        # 마운트가 못 읽었고 본문이 있을 수 있는 종류라면 API 로 한 번 더
        if not body and how in ("오류:TimeoutError", "오류:FileNotFoundError",
                                "오류:OSError", "추출실패"):
            body, how = extract_api(rel, ext, live[rel])
        if not body:
            unread[how] = unread.get(how, 0) + 1
        lvl, why, mode = classify(rel, body, how)
        old = led.get(rel)
        if old:
            sid = old["보안ID"]
            if old["보안등급"] != lvl:
                changed.append((rel, old["보안등급"], lvl, why))
        else:
            nid += 1
            sid = f"SEC-{nid:06d}"
        d, n = os.path.split(rel)
        rows[rel] = {"보안ID": sid, "보안등급": lvl, "파일경로": d or ".", "파일명": n,
                     "근거": why, "판정방식": mode}
        stat[lvl] += 1
        if i % 300 == 0:
            print(f"  … {i}/{len(targets)}")

    print(f"\n■ 이번 판정 {dict(stat)}")
    print(f"■ 본문 못 읽음: {unread}")
    if changed:
        print(f"\n■ 기존 등급이 바뀐 것 {len(changed)}")
        for rel, o, n, why in changed[:25]:
            print(f"   {o}→{n}  {rel[-72:]}  ({why})")
    up = [r for r in rows.values() if r.get("판정방식") == "본문"]
    print(f"\n■ 대장 전체 {len(rows)}행 · 그중 본문 근거 {len(up)}행")

    if not a.apply:
        print("\n[dry-run] --apply 로 기록")
        return 0

    tmp = Path.home() / "{{WORKSPACE_NAME}}/state/보안대장.tsv"
    tmp.parent.mkdir(parents=True, exist_ok=True)
    with tmp.open("w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=HEAD, delimiter="\t", extrasaction="ignore")
        w.writeheader()
        for k in sorted(rows):
            r = dict(rows[k]); r.setdefault("판정방식", "파일명")
            w.writerow(r)
    from lib.ssot_io import put                               # noqa: PLC0415
    ok, msg = put(tmp, LEDGER_REL)
    print(f"{'✓' if ok else '✗'} 대장 기록 {len(rows)}행 — {msg}")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
