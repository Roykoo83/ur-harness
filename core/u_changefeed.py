#!/usr/bin/env python3
"""U 변경 추적 — 보완기획 §3 (운영자 결정 2026-09-04: 전체 미러 대신 변경분 팔로우)

**32GB 전체 미러는 만들지 않는다.** SSOT 를 보는 사람·AI 가 "U 에서 최근 무엇이 바뀌었나"
를 알 수 있으면 된다. 이것이 없어서 KTL 사고가 났다 — 팀이 8/24~9/3 사이 U 에 60건을
만들었는데 로컬 마운트는 8/26 상태를 최신인 척 보여주고, 우리 시스템 어디에도 흔적이 없었다.

Drive API(rclone U_ro) 로 목록을 받아 최근 N일 변경분을 SSOT 에 표로 남긴다.
마운트를 읽지 않는다 — 마운트가 거짓말한 것이 사고의 원인이었다.

사용: u_changefeed.py [--days 7] [--dry-run]
"""
from __future__ import annotations
import argparse, json, subprocess, sys, time, unicodedata
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from lib import hb
from lib.config import cfg, paths

SERVICE = "u-changefeed"
KST = timezone(timedelta(hours=9))
DEST = "SSOT_rw:_시스템/상태"
OUT_NAME = "U_변경_최근.md"


def nfc(s: str) -> str:
    return unicodedata.normalize("NFC", s)


# U 를 읽는 두 경로. 서비스계정이 U 멤버가 되면 첫 항목만 쓰인다.
# 두 번째는 운영자 개인 OAuth 토큰 — 공용 client_id 쿼터를 쓰므로 쓰지 않는 것이 좋지만,
# 서비스계정 승인을 기다리는 동안 U 를 못 보는 것보다는 낫다 (2026-09-07 실측 가능 확인).
def _u_remotes() -> list[tuple[list[str], str]]:
    """U 를 읽는 경로. **설정이 정한다** — 코드에 원격 이름을 박지 않는다.

    서비스계정은 U 공유 드라이브 멤버가 아니다(404, 2026-10-22 실측). 멤버로
    등록되면 설정의 `u.remote` 만 그 원격으로 바꾸면 코드는 그대로 돈다.
    """
    from lib import u_api                                      # noqa: PLC0415
    return [([u_api.remote()], cfg("u.remote") or "u.remote")]


U_REMOTES = _u_remotes()

FETCH_VIA = None  # 실제로 성공한 경로. 산출물 각주와 하트비트에 남긴다.


def fetch(timeout=600) -> list[dict]:
    """U 전량 목록(경로·크기·수정시각). 다운로드는 하지 않는다."""
    global FETCH_VIA
    errs = []
    for args, label in U_REMOTES:
        p = subprocess.run(["rclone", "lsjson", "-R", "--files-only", "--no-mimetype",
                            "--fast-list", *args],
                           capture_output=True, text=True, timeout=timeout)
        if p.returncode == 0 and p.stdout.strip():
            FETCH_VIA = label
            if label != "service_account":
                print(f"경고: {label} 로 U 를 읽었다. 서비스계정 승인이 아직 안 됐다.",
                      file=sys.stderr)
            return json.loads(p.stdout)
        errs.append(f"{label}: rc={p.returncode} {p.stderr.strip()[-160:]}")
    raise RuntimeError("U 목록 실패 — " + " | ".join(errs))


def render(items: list[dict], days: int) -> tuple[str, int]:
    cut = datetime.now(timezone.utc) - timedelta(days=days)
    recent = []
    for it in items:
        try:
            t = datetime.fromisoformat(it["ModTime"].replace("Z", "+00:00"))
        except (KeyError, ValueError):
            continue
        if t >= cut:
            recent.append((t, nfc(it.get("Path", "")), it.get("Size", 0)))
    recent.sort(reverse=True)

    by_dir = defaultdict(list)
    for t, path, size in recent:
        by_dir["/".join(path.split("/")[:3]) or "(루트)"].append((t, path, size))

    now = datetime.now(KST).strftime("%Y-%m-%d %H:%M")
    L = [f"# U 드라이브 최근 {days}일 변경",
         "",
         f"> 갱신 {now} · `u-changefeed` 자동 생성 · 원천 U 전량 {len(items):,}건 중 **{len(recent)}건 변경**",
         "> **로컬 Google Drive 마운트가 아니라 Drive API 로 직접 조회한 결과입니다.**",
         "> 마운트는 8일치 낡은 상태를 최신인 척 보여준 전력이 있습니다(2026-09-04 KTL 사고).",
         f"> 조달 경로: `{FETCH_VIA}`" + ("" if FETCH_VIA == "service_account"
             else " — **임시**. 서비스계정 `{{SERVICE_ACCOUNT}}` 뷰어 승인 대기."),
         "",
         "## 폴더별 요약", "", "| 폴더 | 변경 | 최신 |", "|---|---:|---|"]
    for d, rows in sorted(by_dir.items(), key=lambda kv: -len(kv[1])):
        L.append(f"| `{d}` | {len(rows)} | {rows[0][0].astimezone(KST):%m-%d %H:%M} |")
    L += ["", f"## 변경 파일 (최신순, 최대 300건)", "", "| 시각 | 크기 | 경로 |", "|---|---:|---|"]
    for t, path, size in recent[:300]:
        L.append(f"| {t.astimezone(KST):%m-%d %H:%M} | {size:,} | `{path}` |")
    if len(recent) > 300:
        L.append(f"\n_… 외 {len(recent)-300}건 생략_")
    L += ["", "---", "- 이 파일은 자동 생성입니다. 편집하지 마세요 — 다음 주기에 덮어써집니다.",
          "- 원본은 U 드라이브입니다. SSOT 는 목록만 봅니다.",
          "- 근거: `30_시스템/00_설계/데이터취득계층_보완기획_260904.md` §3"]
    return "\n".join(L) + "\n", len(recent)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=7)
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    t0 = time.time()
    try:
        items = fetch()
    except Exception as e:                                        # noqa: BLE001
        msg = f"{type(e).__name__}: {e}"
        if "Shared drive not found" in msg or "notFound" in msg:
            hb.blocked(SERVICE, "서비스계정이 U 드라이브 멤버가 아님 (404)",
                       needs="U 드라이브 관리자가 {{SERVICE_ACCOUNT}} 을 뷰어로 추가")
        else:
            hb.fail(SERVICE, msg)
        print(f"✗ U 조회 실패 — {msg[:220]}")
        print("  → 서비스계정이 U 드라이브 멤버가 아닙니다. U 관리자가 아래를 추가해야 합니다:")
        print("     {{SERVICE_ACCOUNT}} · 역할 뷰어")
        return 2
    body, n = render(items, a.days)
    local = paths.state / OUT_NAME
    local.write_text(body, encoding="utf-8")
    print(f"U 전량 {len(items):,}건 · 최근 {a.days}일 변경 {n}건 · {time.time()-t0:.0f}s")
    if a.dry_run:
        print(f"[dry-run] 로컬만 기록: {local}")
        return 0
    p = subprocess.run(["rclone", "copyto", str(local), f"{DEST}/{OUT_NAME}"],
                       capture_output=True, text=True, timeout=180)
    if p.returncode != 0:
        hb.fail(SERVICE, f"SSOT 업로드 실패: {p.stderr.strip()[-200:]}")
        print(f"✗ 업로드 실패 rc={p.returncode}")
        return 1
    cur = {"kind": "u_files", "value": len(items)}
    outs = [{"path": f"_시스템/상태/{OUT_NAME}", "count": n}]
    cnt = {"u_total": len(items), "changed": n, "elapsed_s": round(time.time()-t0, 1)}
    if FETCH_VIA == "service_account":
        hb.ok(SERVICE, cursor=cur, outputs=outs, counters=cnt)
    else:
        # 산출물은 정상이지만 조달 경로가 임시다. ok 로 덮으면 서비스계정 승인을
        # 영구히 잊어버린다 — 그래서 partial 로 남긴다.
        hb.partial(SERVICE, targets={"u_list": FETCH_VIA}, cursor=cur,
                   outputs=outs, counters=cnt,
                   note=f"U 를 {FETCH_VIA} 로 읽음. 서비스계정 승인 대기 중.")
    print(f"✓ SSOT 갱신: _시스템/상태/{OUT_NAME}  (조달: {FETCH_VIA})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
