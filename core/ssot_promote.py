#!/usr/bin/env python3
"""U→SSOT 승격 — 데이터취득계층_보완기획_260904.md §4

3주간(8/13~9/4) 멈춰 있던 메일·캘린더·부서방 기록을 SSOT로 흘린다.
승격 스크립트가 **애초에 없었다** — 8/10 골격 구축 때 1회 수동 복사가 전부였다.

원천을 U 드라이브가 아니라 **로컬 nworks stage** 로 잡는다:
  ① U 접근(서비스계정 멤버십)이 아직 없다  ② 경유가 한 단 줄어 실패면이 줄어든다
  ③ nworks-agent 가 매시간 여기에 쓰고 있음이 확인됐다

목적지 쓰기는 DriveFS 마운트가 아니라 **Drive API(rclone SSOT_rw)** 다.
마운트는 8일치 스냅샷을 최신인 척 보여준 전력이 있다(§0 #1~#3).

S3 필터: 파일명 휴리스틱(frontmatter sensitivity)은 쓰지 않는다 — KTL 기술명세서 같은
필수 정보를 오탐으로 막았다. **본문에 실제 주민번호·계좌가 있는 것만** 제외한다(실측 358건 중 1건).
분류는 보안대장(classify_security.py)이 사후에 한다.

사용: ssot_promote.py [--dry-run] [--verbose]
"""
from __future__ import annotations
import argparse, json, os, re, shutil, subprocess, sys, tempfile, time, unicodedata
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from lib import hb

def _P(name: str, sub: str = ""):
    """표준 경로 — 설정이 정한 작업공간 아래. 예전 형제-프로젝트 경로를 대신한다."""
    import sys as _s
    from pathlib import Path as _Path
    _s.path.insert(0, str(_Path(__file__).resolve().parents[1] / "core"))
    from lib.config import paths                                 # noqa: PLC0415
    if name == "raw":
        return paths.raw(sub)
    v = getattr(paths, name)
    return (v / sub) if sub else v


SERVICE = "ssot-promote"
STAGE = _P("stage")
REMOTE = "SSOT_rw:"
TIMEOUT = int(os.environ.get("PROMOTE_TIMEOUT", "300"))  # 전량 재업로드 때는 PROMOTE_TIMEOUT 으로 늘린다

# 본문 스캔 — 분류기(classify_security.py)와 동일 정규식
RRN = re.compile(r"\b\d{6}\s*[-–]\s*[1-4]\d{6}\b")
ACCT = re.compile(r"(계좌번호|예금주|통장사본|입금계좌)[^\n]{0,40}\d{6,}")

# (원천 상대경로, SSOT 목적지, 파일명 접두)
# 메일은 계열이 둘이고 **둘 다 같은 폴더**로 간다 — 접두가 둘을 구분한다.
#   {{BOT_USER}}  live    received >= T0  매시간 증가. 커서(mailId)는 이쪽만 본다
#   {{OPERATOR_USER}}    frozen  received <  T0  563건 고정 (2026-06~08/12). 과거 코퍼스
# 접두는 **mail_curate.SERIES 가 소유한다.** 여기에 따로 적으면 둘이 어긋나고,
# 어긋나면 다른 계열의 메일이 남의 이름으로 올라가 기존 파일을 덮어쓴다
# (2026-10-21 실측: 봇 메일 54건이 개인 계열 이름으로 올라갔다).
def _series_prefix() -> dict[str, str]:
    """stage 폴더명 → SSOT 접두. 못 읽으면 멈춘다 — 추측하지 않는다."""
    import sys as _s
    _s.path.insert(0, str(Path(__file__).resolve().parents[1] / "agents" / "nworks"))
    from mail_curate import SERIES                            # noqa: PLC0415
    return {f"mail_{k}": pre for k, (_st, pre) in SERIES.items()}


_PRE = _series_prefix()

TARGETS = [
    (k, "{{DEPT_ROOT}}/40_커뮤니케이션/메일", _PRE[k]) for k in sorted(_PRE)
] + [
    ("mail_index",    "{{DEPT_ROOT}}/40_커뮤니케이션/메일", ""),
    ("calendar",      "00_공통/20_일정/naverworks_캘린더",       ""),
    ("messenger",     "{{DEPT_ROOT}}/40_커뮤니케이션/메신저",  ""),
]
LIVE_SOURCE = "mail_{{BOT_USER}}"          # 커서가 의미 있는 계열
LEDGER = STAGE / "mail_대장.tsv"       # mail_curate.py 산출. 메일 승격의 전제조건




def load_ledger() -> dict[str, str] | None:
    """물리키 → 승격 여부(Y/N). 대장이 없으면 None.

    **업무에 필요한 메일만 SSOT 로 보낸다**(운영자 2026-10-01). 그 판정은 여기서 하지 않고
    대장이 한다 — 규칙 소유는 `config/brief_filter.json` 하나다. 여기는 집행만 한다.
    """
    if not LEDGER.exists():
        return None
    out = {}
    with LEDGER.open(encoding="utf-8") as fh:
        head = fh.readline().rstrip("\n").split("\t")
        try:
            i_name, i_prom = head.index("ssot_name"), head.index("promote")
        except ValueError:
            return None
        for line in fh:
            c = line.rstrip("\n").split("\t")
            if len(c) > max(i_name, i_prom):
                out[c[i_name]] = c[i_prom]
    return out or None


def nfc(s: str) -> str:
    return unicodedata.normalize("NFC", s)


def blocked(p: Path) -> str | None:
    """본문에 실제 주민번호·계좌가 있으면 사유, 없으면 None."""
    try:
        if p.stat().st_size > 2_000_000:
            return None
        t = p.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return None
    if RRN.search(t):
        return "주민등록번호"
    if ACCT.search(t):
        return "계좌"
    return None


def rclone(*args, timeout=TIMEOUT) -> tuple[int, str, str]:
    p = subprocess.run(["rclone", *args], capture_output=True, text=True, timeout=timeout)
    return p.returncode, p.stdout, p.stderr


def build_staging(src_root: Path, prefix: str, work: Path,
                  ledger: dict[str, str] | None = None) -> tuple[int, list, int, dict]:
    """원천을 staging 으로 복제(접두·NFC 적용). (파일수, 제외목록, 최대 mailId, 걸러낸 수)

    ledger 가 주어지면 **업무 무관(promote=N)·대장 미등재는 복제하지 않는다.**
    대장에 없는 것을 올리지 않는 쪽을 택한다 — 올려놓고 나중에 지우는 것보다
    안 올리고 건수로 드러나는 편이 되돌리기 쉽다.
    """
    n, excluded, max_id = 0, [], 0
    filt = {"업무무관": 0, "대장미등재": 0, "stage색인": 0}
    for f in sorted(src_root.rglob("*.md")):
        rel = Path(*[nfc(x) for x in f.relative_to(src_root).parts])
        name = prefix + rel.name if prefix else rel.name
        if ledger is not None and prefix:            # 메일 계열만 대장 판정을 받는다
            key = re.match(r"^(\S+?-\d+)_", name)
            if not key:
                # 메일 1통이 아니다 = stage 의 `index.md`. 그것은 **전량 목록**이라
                # SSOT 로 올리면 승격 안 된 메일을 가리키는 깨진 링크가 된다.
                # SSOT 색인은 대장에서 따로 만든다 (mail_index 타겟).
                filt["stage색인"] += 1
                continue
            verdict = ledger.get(key.group(1))
            if verdict is None:
                filt["대장미등재"] += 1
                continue
            if verdict != "Y":
                filt["업무무관"] += 1
                continue
        why = blocked(f)
        if why:
            excluded.append((nfc(f.name), why))
            continue
        dst = work / rel.parent / name
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(f, dst)
        n += 1
        m = re.match(r"^(?:\S+?-)?(\d+)_", name)
        if m:
            max_id = max(max_id, int(m.group(1)))
    return n, excluded, max_id, filt


def promote(src_name: str, dest: str, prefix: str, dry: bool, verbose: bool,
            ledger: dict[str, str] | None = None) -> dict:
    src = STAGE / src_name
    r = {"source": src_name, "dest": dest, "status": "fail", "files": 0, "excluded": []}
    is_mail = src_name.startswith("mail_")
    if is_mail and ledger is None:
        # 대장 없이 메일을 올리면 업무 무관 메일이 섞인다. 결론 내지 말고 멈춘다.
        r["status"] = "blocked"
        r["reason"] = f"정리대장 없음({LEDGER.name}) — mail_curate.py 를 먼저 돌린다"
        return r
    if not src.is_dir():
        # 이미 승격이 끝난 frozen 계열은 stage 가 없다. **그것은 실패가 아니다.**
        # 매번 fail 로 보고하면 진짜 실패가 묻히고 status 가 영영 partial 에 머문다.
        # 다만 live 계열의 stage 가 없는 것은 진짜 문제다 — 둘을 구분한다.
        frozen = src_name.startswith("mail_") and src_name not in (LIVE_SOURCE,)
        r["status"] = "skip" if frozen else "fail"
        r["reason"] = (f"해당 없음 (stage 없음 — 승격 완료된 계열): {src.name}"
                       if frozen else f"원천 없음: {src}")
        return r
    work = Path(tempfile.mkdtemp(prefix=f"promote-{src_name}-"))
    try:
        n, excluded, max_id, filt = build_staging(src, prefix, work,
                                                  ledger if is_mail else None)
        r.update(files=n, excluded=excluded, max_id=max_id, filtered=filt)
        if dry:
            r["status"] = "dry"
            return r
        if n == 0:
            r["status"] = "ok"
            return r
        rc, _, err = rclone("copy", str(work), f"{REMOTE}{dest}", "--checksum",
                            "--transfers", "4", "--stats", "0")
        if rc != 0:
            r["reason"] = f"copy rc={rc}: {err.strip()[-200:]}"
            return r
        # 도착 검증 — 마운트 존재 확인이 아니라 API 해시 대조
        rc, _, err = rclone("check", str(work), f"{REMOTE}{dest}", "--one-way", "--checksum")
        if rc != 0:
            r["reason"] = f"check 불일치 rc={rc}: {err.strip()[-200:]}"
            return r
        r["status"] = "ok"
        r["verified"] = "rclone check --one-way 0 diff"
    except subprocess.TimeoutExpired:
        r["reason"] = f"타임아웃 {TIMEOUT}s"
    except Exception as e:                      # noqa: BLE001
        r["reason"] = f"{type(e).__name__}: {e}"
    finally:
        shutil.rmtree(work, ignore_errors=True)
    return r


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--verbose", action="store_true")
    a = ap.parse_args()
    t0 = time.time()
    ledger = load_ledger()
    if ledger is None:
        print(f"! 정리대장 없음 — 메일 승격을 건너뛴다 ({LEDGER})")
    results = [promote(s, d, p, a.dry_run, a.verbose, ledger) for s, d, p in TARGETS]

    for r in results:
        mark = {"ok": "✓", "dry": "·", "fail": "✗", "blocked": "⊘",
                "skip": "—"}.get(r["status"], "?")
        print(f"{mark} {r['source']:16} → {r['dest']}")
        f = r.get("filtered") or {}
        print(f"    파일 {r['files']}건" + (f" · 최대 id {r['max_id']}" if r.get("max_id") else "")
              + (f" · 업무무관 {f['업무무관']} 제외" if f.get("업무무관") else "")
              + (f" · 대장미등재 {f['대장미등재']} 보류" if f.get("대장미등재") else "")
              + (f" · {r.get('verified','')}" if r.get("verified") else ""))
        for name, why in r["excluded"]:
            print(f"    제외[{why}] {name[:70]}")
        if r.get("reason"):
            print(f"    사유: {r['reason']}")

    targets = {r["source"]: r["status"] for r in results}
    total = sum(r["files"] for r in results)
    # 커서는 **live 계열만** 본다. frozen(roy-)은 mailId 공간이 달라 섞으면 커서가 오염된다
    # (운영자 최대 778 > rbp 최대 754 — 섞으면 신규 메일을 "이미 본 것"으로 오판한다).
    max_id = next((r.get("max_id", 0) for r in results if r["source"] == LIVE_SOURCE), 0)
    excl = sum(len(r["excluded"]) for r in results)
    off = sum((r.get("filtered") or {}).get("업무무관", 0) for r in results)
    pend = sum((r.get("filtered") or {}).get("대장미등재", 0) for r in results)
    cursor = {"kind": "mailId", "value": max_id}
    outputs = [{"path": r["dest"], "count": r["files"]} for r in results]
    counters = {"files": total, "excluded_s3": excl, "filtered_offtopic": off,
                "pending_unledgered": pend, "elapsed_s": round(time.time() - t0, 1)}

    if a.dry_run:
        print(f"\n[dry-run] 승격 예정 {total}건 · 업무무관 {off}건 제외 · "
              f"대장미등재 {pend}건 보류 · S3 제외 {excl}건 · {counters['elapsed_s']}s")
        return 0
    if any(st == "blocked" for st in targets.values()):
        hb.blocked(SERVICE, "; ".join(r.get("reason", "?") for r in results
                                      if r["status"] == "blocked"),
                   needs="mail_curate.py 실행")
        print(f"\n막힘 — {targets}")
        return 2
    # skip = 「해당 없음」이다. 실패가 아니다 — 이것을 실패로 세면
    # 승격이 매번 성공해도 status 가 영영 partial 에 머물고, 게이트가 그것을 위반으로 본다
    oks = [s for s, st in targets.items() if st in ("ok", "skip")]
    if len(oks) == len(results):
        hb.ok(SERVICE, cursor=cursor, outputs=outputs, counters=counters)
        print(f"\n승격 완료 {total}건 · 업무무관 {off}건 제외 · 대장미등재 {pend}건 보류 "
              f"· S3 제외 {excl}건 · {counters['elapsed_s']}s")
        return 0
    if oks:
        hb.partial(SERVICE, targets=targets, cursor=cursor, outputs=outputs, counters=counters)
        print(f"\n부분 성공 — {targets}")
        return 1
    hb.fail(SERVICE, "; ".join(r.get("reason", "?") for r in results), cursor=cursor)
    print(f"\n전체 실패 — {targets}")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
