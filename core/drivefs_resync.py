#!/usr/bin/env python3
"""DriveFS 캐시 초기화 + 마운트↔클라우드 전체 재동기화 — 운영자 승인 작업 (2026-10-01)

왜 필요한가
  마운트가 changelog 루프에 빠져 클라우드를 못 따라온다. 실측: SSOT 메일 API 1,031 /
  마운트 562. 재시작으로 풀리지 않았다(활동 로그는 있는데 25분간 적용 0건).
  다음 조치는 계정 캐시 삭제 → 전체 재동기화다.

무엇이 위험한가
  **캐시를 지우면 업로드 대기 큐도 사라진다.** 마운트에만 있고 아직 클라우드에
  올라가지 않은 파일은 그대로 유실된다. 그래서 이 스크립트는 그것을 **먼저 찾아
  백업하고, 백업이 실패하면 초기화하지 않는다.**

  고장난 것은 이 맥의 마운트뿐이고 클라우드 데이터는 정상이다 — 되돌릴 수 없는 쪽은
  "클라우드에 없는 로컬 파일" 하나뿐이다. 거기에만 안전장치를 집중한다.

단계
  1 preflight  마운트에만 있는 파일 탐지 → 백업 (실패하면 중단)
  2 snapshot   마운트·API 건수 기록
  3 stop       Google Drive 종료
  4 purge      계정 캐시 삭제 ({{OPERATOR_ACCOUNT}} 계정 폴더만)
  5 start      재시작
  6 converge   마운트가 API 와 일치할 때까지 대기
  7 restore    백업분이 클라우드에 없으면 API 로 올린다
  8 reindex    색인 재생성 (가드가 통과해야 쓴다)
  9 report     운영자 1:1 알림 + 로그

사용
  drivefs_resync.py --dry-run     1~2 단계만. 무엇을 백업할지 보여준다
  drivefs_resync.py --yes         전체 실행 (예약 실행이 쓰는 모드)
"""
from __future__ import annotations
import argparse
import json
import os
import shutil
import subprocess
import sys
import time
import unicodedata
from datetime import datetime
from pathlib import Path

HOME = Path.home()
REPO = HOME / "{{WORKSPACE_NAME}}"
CLOUD = HOME / "{{MOUNT_REL}}"
DRIVEFS = HOME / "Library/Application Support/Google/DriveFS"
ACCOUNT_ID = "117235574279793458418"      # {{OPERATOR_ACCOUNT}} — 다른 계정은 건드리지 않는다
U_TEAM_DRIVE = "{{SOURCE_DRIVE_ID}}"

WORK = REPO / "state" / "drivefs_resync"
LOG = WORK / "resync.log"
BACKUP = WORK / "backup"
RECENT_DAYS = 14                           # 원격 비교가 안 되는 트리는 최근 수정분을 백업한다
RECENT_MAX_FILES = 3000                    # 그 백업의 상한 — 넘으면 멈추고 사람에게 넘긴다
RECENT_MAX_BYTES = 3 * 2**30
CONVERGE_TIMEOUT_H = 6
RCLONE = shutil.which("rclone") or "/opt/homebrew/bin/rclone"

# 정밀 비교가 가능한 트리 — (마운트 경로, rclone 원격, 추가 플래그, 필수 여부)
# required=True 면 순회를 완주하지 못하면 **초기화하지 않는다.**
#   SSOT: 사람이 오늘 만든 작업물이 여기 있다. 반드시 완주해야 한다
#         (실측 2026-10-01: 오늘 오후 작업물 2건이 업로드 대기 중이었다)
#   U   : 수집물이 주 내용이고 로컬 output/ 에 원본이 있다. 못 끝내면 경고하고 진행한다 —
#         고장난 마운트 때문에 순회가 안 끝나는데 그 때문에 고칠 수 없다면 교착이다
TREES = [
    (CLOUD / "공유 드라이브/{{SSOT_DRIVE}}", "SSOT_ro:", [], True),
    (CLOUD / "공유 드라이브/U", "gdrive:", ["--drive-team-drive", U_TEAM_DRIVE], False),
]
WALK_LIMIT_S = 3600            # 트리당 순회 상한
# 쓰기용 원격 — 복원할 때만 쓴다
WRITE_REMOTE = {
    "공유 드라이브/{{SSOT_DRIVE}}": ("SSOT_rw:", []),
    "공유 드라이브/U": ("gdrive:", ["--drive-team-drive", U_TEAM_DRIVE]),
}
SKIP_NAMES = {".DS_Store", ".localized", "Icon\r"}


def nfc(s: str) -> str:
    return unicodedata.normalize("NFC", s)


def log(msg: str) -> None:
    line = f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] {msg}"
    print(line, flush=True)
    LOG.parent.mkdir(parents=True, exist_ok=True)
    with LOG.open("a", encoding="utf-8") as fh:
        fh.write(line + "\n")


def notify(msg: str) -> None:
    """운영자 1:1 알림. 실패해도 작업은 계속한다 — 로그가 정본이다."""
    try:
        sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "agents"))
        from nworks.client import NworksClient
        from nworks.config import load_config
        from nworks.domains import bot_send_text
        sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "agents" / "nworks"))
        from nworks_cycle import OPERATOR_USER_ID          # user_id 는 코드 상수다(config 에 없다)
        bot_send_text(NworksClient(), load_config()["bot_id"],
                      f"[{{ORG_NAME}} AX Bot] DriveFS 재동기화\n{msg[:900]}",
                      user_id=OPERATOR_USER_ID)
        log("  (운영자 알림 발송)")
    except Exception as e:                                  # noqa: BLE001
        log(f"  (알림 실패: {type(e).__name__} {str(e)[:120]} — 로그를 보면 된다)")


def rclone(*args, timeout=1800) -> tuple[int, str, str]:
    try:
        p = subprocess.run([RCLONE, *args], capture_output=True, text=True, timeout=timeout)
        return p.returncode, p.stdout, p.stderr
    except subprocess.TimeoutExpired:
        return 124, "", f"timeout {timeout}s"


def mount_listing(root: Path, limit_s: float = WALK_LIMIT_S) -> dict[str, Path]:
    """마운트의 상대경로(NFC) → 실제 경로. dataless 여부는 묻지 않는다(stat 만 쓴다).

    고장난 마운트는 순회 자체가 느리다 — 상한을 넘으면 TimeoutError 로 알린다.
    """
    out: dict[str, Path] = {}
    end = time.time() + limit_s
    for dp, dn, fn in os.walk(root, onerror=lambda e: None):
        if time.time() > end:
            raise TimeoutError(f"{root.name} 순회가 {limit_s / 60:.0f}분을 넘었다 "
                               f"({len(out)}건까지 읽음)")
        dn[:] = [d for d in dn if not d.startswith(".")]
        for f in fn:
            if f.startswith(".") or f in SKIP_NAMES:
                continue
            p = Path(dp) / f
            try:
                out[nfc(str(p.relative_to(root)))] = p
            except ValueError:
                continue
    return out


def api_listing(remote: str, flags: list[str]) -> set[str] | None:
    rc, so, se = rclone("lsf", "-R", "--files-only", remote, *flags, timeout=2700)
    if rc != 0:
        log(f"  ! API 목록 실패 {remote}: {se.strip()[-160:]}")
        return None
    return {nfc(l) for l in so.splitlines() if l.strip()}


def phase1_preflight() -> dict:
    """마운트에만 있는 파일을 찾아 백업한다. 여기서 실패하면 초기화하지 않는다."""
    log("── 1 preflight: 클라우드에 없는 로컬 파일 탐지")
    BACKUP.mkdir(parents=True, exist_ok=True)
    result = {"trees": [], "backed_up": 0, "unreadable": [], "ok": True}

    for root, remote, flags, required in TREES:
        rel_root = str(root.relative_to(CLOUD))
        if not root.is_dir():
            log(f"  ! 마운트 없음: {rel_root} — 건너뛴다")
            result["trees"].append({"tree": rel_root, "status": "no_mount"})
            if required:
                result["ok"] = False
            continue
        log(f"  {rel_root}: 마운트 순회 중…")
        try:
            mnt = mount_listing(root)
        except TimeoutError as e:
            log(f"  ! {e}")
            result["trees"].append({"tree": rel_root, "status": "walk_timeout",
                                    "required": required})
            if required:
                log("    이 트리는 필수다 — 초기화하지 않는다")
                result["ok"] = False
            else:
                log("    필수 아님 — 경고로 남기고 진행한다 (로컬 output/ 에 원본이 있다)")
            continue
        log(f"    마운트 {len(mnt)}건 · API 조회 중…")
        api = api_listing(remote, flags)
        if api is None:
            # API 를 못 읽으면 무엇이 대기 중인지 알 수 없다 → 필수 트리면 초기화하면 안 된다
            if required:
                result["ok"] = False
            result["trees"].append({"tree": rel_root, "status": "api_failed",
                                    "mount": len(mnt)})
            continue
        only_local = sorted(set(mnt) - api)
        log(f"    API {len(api)}건 · 로컬에만 {len(only_local)}건")
        saved, bad = 0, []
        for rel in only_local:
            src = mnt[rel]
            dst = BACKUP / rel_root / rel
            try:
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src, dst)
                saved += 1
            except OSError as e:
                # dataless 자리표시자이거나 잠긴 파일. 업로드 대기 파일은 실체가 있어 읽힌다 —
                # 못 읽는 것은 "클라우드에 있는데 목록에서 빠진 것"일 수 있어 사유를 남긴다.
                bad.append({"path": f"{rel_root}/{rel}", "error": f"{type(e).__name__}: {e}"})
        result["backed_up"] += saved
        result["unreadable"] += bad
        result["trees"].append({"tree": rel_root, "status": "ok", "mount": len(mnt),
                                "api": len(api), "only_local": len(only_local),
                                "saved": saved, "unreadable": len(bad)})
        log(f"    백업 {saved}건" + (f" · 읽기 실패 {len(bad)}건" if bad else ""))

    # 원격 비교가 없는 트리(내 드라이브 등)는 최근 수정분을 보수적으로 백업한다.
    # 상한을 둔다 — 상한 없는 복사는 디스크를 채운다.
    # 실측 2026-10-01: 공유 드라이브는 U·SSOT 둘뿐이라 위에서 전부 덮었고,
    # 내 드라이브의 14일 내 수정은 0건이었다. 그래도 언제든 늘 수 있다.
    cutoff = time.time() - RECENT_DAYS * 86400
    covered = [str(t[0]) for t in TREES]
    recent, bytes_sum, skipped = 0, 0, 0
    for base in [x for x in CLOUD.iterdir() if x.is_dir()]:
        for dp, dn, fn in os.walk(base, onerror=lambda e: None):
            if any(dp.startswith(c) for c in covered):
                dn[:] = []
                continue
            dn[:] = [x for x in dn if not x.startswith(".")]
            for f in fn:
                if f.startswith(".") or f in SKIP_NAMES:
                    continue
                src = Path(dp) / f
                try:
                    st = src.stat()
                    if st.st_mtime < cutoff:
                        continue
                    if recent >= RECENT_MAX_FILES or bytes_sum + st.st_size > RECENT_MAX_BYTES:
                        skipped += 1
                        continue
                    dst = BACKUP / "_최근수정" / nfc(str(src.relative_to(CLOUD)))
                    dst.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(src, dst)
                    recent += 1
                    bytes_sum += st.st_size
                    result["backed_up"] += 1
                except OSError:
                    continue
    result["recent_backed_up"] = recent
    result["recent_skipped_over_limit"] = skipped
    if skipped:
        log(f"  ! 최근수정 백업 상한 초과로 {skipped}건 건너뜀 "
            f"(상한 {RECENT_MAX_FILES}건 / {RECENT_MAX_BYTES // 2**30}GB) — "
            f"이 트리는 클라우드와 비교하지 못하므로 사람이 확인해야 한다")
        result["ok"] = False
    log(f"  최근수정 백업 {recent}건 ({bytes_sum / 2**20:.0f}MB)")
    log(f"  백업 합계 {result['backed_up']}건 → {BACKUP}")
    (WORK / "preflight.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
    return result


def phase2_snapshot() -> dict:
    log("── 2 snapshot: 상태 기록")
    snap = {"when": datetime.now().isoformat(timespec="seconds"), "trees": []}
    for root, remote, flags, _req in TREES:
        rel = str(root.relative_to(CLOUD))
        try:
            m = len(mount_listing(root, 900)) if root.is_dir() else -1
        except TimeoutError:
            m = -1
        a = api_listing(remote, flags)
        snap["trees"].append({"tree": rel, "mount": m, "api": len(a) if a else -1})
        log(f"  {rel}: 마운트 {m} / API {len(a) if a else '조회실패'}")
    (WORK / "snapshot_before.json").write_text(
        json.dumps(snap, ensure_ascii=False, indent=1), encoding="utf-8")
    return snap


def phase3_stop() -> bool:
    log("── 3 stop: Google Drive 종료")
    # 우리 자동화가 마운트에 쓰는 중이면 기다린다
    for _ in range(30):
        busy = subprocess.run(
            ["pgrep", "-f", "nworks_cycle|ssot_promote|publish_dashboards|build_index"],
            capture_output=True, text=True).stdout.strip()
        if not busy:
            break
        log("  우리 자동화가 마운트를 쓰는 중 — 20초 대기")
        time.sleep(20)
    subprocess.run(["pkill", "-TERM", "-f",
                    "Google Drive.app/Contents/MacOS/Google Drive"], capture_output=True)
    for _ in range(15):
        time.sleep(2)
        if not subprocess.run(["pgrep", "-f", "MacOS/Google Drive"],
                              capture_output=True, text=True).stdout.strip():
            log("  종료됨")
            return True
    subprocess.run(["pkill", "-9", "-f",
                    "Google Drive.app/Contents/MacOS/Google Drive"], capture_output=True)
    time.sleep(5)
    alive = subprocess.run(["pgrep", "-f", "MacOS/Google Drive"],
                           capture_output=True, text=True).stdout.strip()
    log("  강제 종료" if not alive else f"  ! 아직 살아 있다: {alive}")
    return not alive


def phase4_purge() -> bool:
    log("── 4 purge: 계정 캐시 삭제")
    acc = DRIVEFS / ACCOUNT_ID
    if not acc.is_dir():
        log(f"  ! 계정 폴더 없음: {ACCOUNT_ID} — 이미 지워졌거나 ID 가 바뀌었다")
        return False
    size = subprocess.run(["du", "-sh", str(acc)], capture_output=True,
                          text=True).stdout.split()[0]
    # 지우지 않고 **옮긴다.** 되돌릴 길을 남긴다 — 재로그인을 요구하면 이것을 되돌린다.
    quar = WORK / f"cache_quarantine_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    quar.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(acc), str(quar / ACCOUNT_ID))
    log(f"  {ACCOUNT_ID} ({size}) → {quar}  (삭제가 아니라 격리 — 되돌릴 수 있다)")
    log(f"  다른 계정({len(list(DRIVEFS.glob('[0-9]*')))}개)은 건드리지 않았다")
    return True


def phase5_start() -> bool:
    log("── 5 start: Google Drive 재시작")
    subprocess.run(["open", "-a", "Google Drive"], capture_output=True)
    for i in range(60):
        time.sleep(5)
        if (CLOUD / "공유 드라이브").is_dir():
            log(f"  마운트 복귀 ({(i + 1) * 5}초)")
            return True
    log("  ! 5분 내 마운트가 돌아오지 않았다 — 로그인 화면이 떴을 수 있다")
    return False


def phase6_converge() -> dict:
    """마운트가 API 와 일치할 때까지 기다린다. 진전이 멈추면 보고하고 끝낸다."""
    log(f"── 6 converge: 수렴 대기 (최대 {CONVERGE_TIMEOUT_H}시간)")
    targets = []
    for root, remote, flags, _req in TREES:
        a = api_listing(remote, flags)
        targets.append({"root": root, "remote": remote, "flags": flags,
                        "api": len(a) if a else -1,
                        "rel": str(root.relative_to(CLOUD))})
    deadline = time.time() + CONVERGE_TIMEOUT_H * 3600
    stall, last = 0, {}
    while time.time() < deadline:
        time.sleep(180)
        done, line = True, []
        for t in targets:
            try:
                m = len(mount_listing(t["root"], 900)) if t["root"].is_dir() else -1
            except TimeoutError:
                m = -1
            line.append(f"{t['rel']} {m}/{t['api']}")
            if t["api"] > 0 and m < t["api"] * 0.995:
                done = False
            if last.get(t["rel"]) == m:
                stall += 1
            else:
                stall = 0
            last[t["rel"]] = m
        log("  " + " · ".join(line) + (f"  (정지 {stall}회)" if stall else ""))
        if done:
            log("  수렴 완료")
            return {"converged": True, "state": last}
        if stall >= 20:          # 1시간 진전 없음
            log("  ! 1시간 진전 없음 — 수렴 실패로 본다")
            return {"converged": False, "reason": "1시간 진전 없음", "state": last}
    return {"converged": False, "reason": f"{CONVERGE_TIMEOUT_H}시간 초과", "state": last}


def phase7_restore() -> dict:
    """백업한 '클라우드에 없던 파일'이 지금도 없으면 API 로 올린다."""
    log("── 7 restore: 백업분 확인")
    out = {"checked": 0, "uploaded": 0, "failed": []}
    for tree_dir in sorted(BACKUP.glob("*")):
        if not tree_dir.is_dir() or tree_dir.name == "_최근수정":
            continue
        key = tree_dir.name
        wr = WRITE_REMOTE.get(key)
        if not wr:
            log(f"  {key}: 쓰기 원격 없음 — 건너뛴다(마운트가 복구되면 저절로 올라간다)")
            continue
        remote, flags = wr
        api = api_listing(remote, flags)
        for p in sorted(tree_dir.rglob("*")):
            if not p.is_file():
                continue
            rel = nfc(str(p.relative_to(tree_dir)))
            out["checked"] += 1
            if api is not None and rel in api:
                continue
            rc, _, se = rclone("copyto", str(p), f"{remote}{rel}", *flags, timeout=600)
            if rc == 0:
                out["uploaded"] += 1
            else:
                out["failed"].append({"path": f"{key}/{rel}", "err": se.strip()[-120:]})
    log(f"  확인 {out['checked']}건 · 올림 {out['uploaded']}건 · 실패 {len(out['failed'])}건")
    return out


def phase8_reindex() -> str:
    log("── 8 reindex: 색인 재생성")
    bi = CLOUD / "공유 드라이브/{{SSOT_DRIVE}}/_시스템/도구/build_index.py"
    if not bi.exists():
        return "색인기 없음"
    p = subprocess.run([sys.executable, str(bi)], capture_output=True, text=True,
                       timeout=3600)
    msg = (p.stdout + p.stderr).strip().splitlines()
    tail = msg[-1] if msg else f"rc={p.returncode}"
    log(f"  {tail}")
    return tail


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="1~2 단계만")
    ap.add_argument("--yes", action="store_true", help="전체 실행")
    ap.add_argument("--window", default="", metavar="HH:MM-HH:MM",
                    help="이 시각 창 안에서만 실행한다. 밖이면 아무것도 하지 않는다")
    a = ap.parse_args()
    WORK.mkdir(parents=True, exist_ok=True)

    # 예약 실행은 Mac 이 잠들어 있으면 그 시각을 건너뛰고 **깨어날 때** 실행된다.
    # 그대로 두면 아침에 운영자 가 맥을 여는 순간 마운트를 내려버린다. 창을 벗어나면 거른다.
    if a.window:
        try:
            lo, hi = (datetime.strptime(x, "%H:%M").time() for x in a.window.split("-"))
            now = datetime.now().time()
            inside = (lo <= now or now <= hi) if lo > hi else (lo <= now <= hi)
        except ValueError:
            log(f"창 형식 오류: {a.window} — HH:MM-HH:MM")
            return 2
        if not inside:
            log(f"지금({now.strftime('%H:%M')})은 실행 창({a.window}) 밖이다 — 아무것도 하지 않는다")
            return 0

    log("═══ DriveFS 재동기화 시작 ═══")

    pre = phase1_preflight()
    snap = phase2_snapshot()

    if not pre["ok"]:
        msg = ("preflight 실패 — 클라우드 목록을 못 읽어 무엇이 업로드 대기인지 알 수 없다. "
               "캐시를 건드리지 않고 멈췄다.")
        log(f"✗ {msg}")
        notify(msg)
        return 2
    if pre["unreadable"]:
        log(f"  주의: 읽지 못한 로컬 파일 {len(pre['unreadable'])}건 — preflight.json 참조")

    if a.dry_run or not a.yes:
        log("[dry-run] 여기서 멈춘다. 실제 실행은 --yes")
        print(json.dumps({"preflight": pre, "snapshot": snap},
                         ensure_ascii=False, indent=1))
        return 0

    if not phase3_stop():
        notify("Google Drive 를 종료하지 못해 멈췄다. 캐시는 건드리지 않았다.")
        return 2
    if not phase4_purge():
        phase5_start()
        notify("계정 캐시 폴더를 찾지 못해 멈췄다. Drive 는 다시 켰다.")
        return 2
    started = phase5_start()
    if not started:
        notify("재시작 후 5분 내 마운트가 돌아오지 않았다 — **로그인이 필요할 수 있다.** "
               f"사람이 Google Drive 를 열어 로그인하면 재동기화가 이어진다. "
               f"되돌리려면 {WORK} 의 cache_quarantine_* 을 제자리로 옮긴다.")
        return 2

    conv = phase6_converge()
    res = phase7_restore()
    idx = phase8_reindex()

    ok = conv["converged"] and not res["failed"]
    head = "완료" if ok else "부분 완료 — 확인 필요"
    body = (f"백업 {pre['backed_up']}건 · 복원 올림 {res['uploaded']}건"
            f"{' · 실패 ' + str(len(res['failed'])) + '건' if res['failed'] else ''}\n"
            f"수렴: {'성공' if conv['converged'] else conv.get('reason', '실패')} "
            f"{conv.get('state', {})}\n색인: {idx}\n로그: {LOG}")
    log(f"═══ {head} ═══\n{body}")
    notify(f"{head}\n{body}")
    (WORK / "result.json").write_text(json.dumps(
        {"preflight": pre, "before": snap, "converge": conv, "restore": res,
         "reindex": idx, "ok": ok}, ensure_ascii=False, indent=1), encoding="utf-8")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
