"""nworks cycle — mail pull -> parse -> calendar -> messenger -> drive sync (pure python).

주기는 launchd `com.urban.nworks-mail` 의 StartInterval 이 정한다 (현재 3600초 = 1시간).
docstring 에 주기를 박아 두면 plist 를 고쳐도 문서만 낡는다 — plist 가 정본이다.

launchd com.urban.nworks-mail runs this with miniconda python3.
zsh+rsync 버전을 대체: launchd TCC(전체 디스크 접근)는 실행 바이너리 기준이므로
python3 단일 바이너리로 고정하고, rsync 대신 shutil 동기화를 쓴다.
- 00_raw: 추가/갱신만 (원본 불변 — 드라이브 쪽 삭제 없음)
- 10_stage: 미러 (로컬에 없는 파일은 드라이브에서도 삭제)
"""
import datetime
import json
import shutil
import subprocess
import sys
from pathlib import Path

PROJ = Path(__file__).resolve().parents[2]
FOUNDRY = Path.home() / ("{{MOUNT_REL}}/"
                         "공유 드라이브/U/5. Naverworks")
LOG = PROJ / "workspace" / "mail_cycle.log"
PY = sys.executable


def log(msg: str) -> None:
    LOG.parent.mkdir(exist_ok=True)
    with LOG.open("a") as f:
        f.write(f"[{datetime.datetime.now():%Y-%m-%d %H:%M:%S}] {msg}\n")


def run(desc: str, cmd: list) -> None:
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=1800)
    out = (r.stdout or "").strip().splitlines()
    log(f"{desc}: {out[-1] if out else ''}{' [stderr:' + r.stderr.strip()[-200:] + ']' if r.returncode else ''}")
    _watch_auth(desc, r)


# ── UA 토큰 장애 감시 ─────────────────────────────────────────────────────
# 2026-08-25 앱 스코프 변경으로 UA 토큰이 무효화됐는데 **이틀간 아무도 몰랐다.**
# 수집이 매시간 401로 죽어도 로그에만 남고 사람에게 닿지 않았기 때문이다.
# 401이 3회(=3시간) 연속되면 Roy 1:1 로 경고한다 — 승인된 발송 유형(사이클 이상 경고).
# 매번 알리면 무시되므로 6시간에 한 번만 다시 알린다.
_AUTH_STATE = PROJ / "workspace" / "ua_auth_watch.json"


def _watch_auth(desc: str, r) -> None:
    if not desc.startswith("pull["):
        return
    import json as _j, time as _t
    try:
        st = _j.loads(_AUTH_STATE.read_text())
    except Exception:
        st = {"fails": 0, "alerted_at": 0}
    failed = r.returncode != 0 and ("401" in (r.stderr or "") or "unauthorized" in (r.stderr or ""))
    if not failed:
        if st.get("fails"):
            _AUTH_STATE.write_text(_j.dumps({"fails": 0, "alerted_at": 0}))
        return
    st["fails"] = st.get("fails", 0) + 1
    if st["fails"] >= 3 and _t.time() - st.get("alerted_at", 0) > 6 * 3600:
        try:
            sys.path.insert(0, str(PROJ / "scripts"))
            from nworks.client import NworksClient
            from nworks.config import load_config
            from nworks.domains import bot_send_text
            bot_send_text(NworksClient(), load_config()["bot_id"],
                          f"⚠️ 부서 메일 수집이 {st['fails']}시간째 인증 실패(401)입니다.\n"
                          f"복구: HANDOVER.md 「복구 절차 R1」 (nwctl ua-auth {{BOT_USER}})\n"
                          f"(로그: nworks-agent/workspace/mail_cycle.log)",
                          user_id="{{OPERATOR_ACCOUNT}}")
            st["alerted_at"] = _t.time()
            log("UA 토큰 장애 경고 발송 (Roy 1:1)")
        except Exception as e:
            log(f"UA 장애 경고 실패: {str(e)[:120]}")
    _AUTH_STATE.write_text(_j.dumps(st))


def sync_tree(src: Path, dst: Path, mirror: bool) -> int:
    """Copy new/changed files src->dst; mirror=True deletes dst files absent in src."""
    n = 0
    if not src.exists():
        return 0
    for f in src.rglob("*"):
        if not f.is_file():
            continue
        rel = f.relative_to(src)
        d = dst / rel
        try:
            # 드라이브 파일은 절대 읽지 않는다 — 클라우드 전용 파일 materialize가
            # launchd 컨텍스트에서 EDEADLK를 유발. 크기+수정시각(stat)으로만 비교.
            if d.exists():
                ds, fs = d.stat(), f.stat()
                if ds.st_size == fs.st_size and abs(ds.st_mtime - fs.st_mtime) < 2:
                    continue
                d.unlink()  # dataless 파일 덮어쓰기는 EDEADLK — 삭제 후 새로 쓴다
            d.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(f, d)
            n += 1
        except OSError as e:
            log(f"sync 파일 건너뜀 {rel}: {e}")
            sync_tree.skipped = getattr(sync_tree, "skipped", 0) + 1
    if mirror and dst.exists():
        for f in dst.rglob("*"):
            if f.is_file() and not (src / f.relative_to(dst)).exists():
                f.unlink()
                n += 1
    return n


def _enable_dataless_materialization() -> None:
    """launchd 프로세스는 기본적으로 클라우드 전용 파일 접근이 꺼져 있다(EDEADLK).
    IOPOL_TYPE_VFS_MATERIALIZE_DATALESS_FILES(3)을 ON(2)으로 켠다."""
    try:
        import ctypes
        libc = ctypes.CDLL("/usr/lib/libSystem.dylib")
        libc.setiopolicy_np(3, 0, 2)  # type, IOPOL_SCOPE_PROCESS, ON
    except Exception as e:
        log(f"materialize 정책 설정 실패(무시): {e}")


ROY_USER_ID = "c50aed6d-af42-42ef-16a1-03f2f66c3ae7"


def alert_roy(msg: str) -> None:
    """사이클 이상 시 Roy 1:1 경고 (Roy 승인 발송 유형, 2026-08-05)."""
    try:
        sys.path.insert(0, str(PROJ / "scripts"))
        from nworks.client import NworksClient
        from nworks.config import load_config
        from nworks.domains import bot_send_text
        bot_send_text(NworksClient(), load_config()["bot_id"],
                      f"[Urban AX Bot·경고] nworks 사이클 이상\n{msg[:500]}", user_id=ROY_USER_ID)
    except Exception as e:
        log(f"경고 발송 실패: {str(e)[:100]}")


def build_messenger_stage() -> int:
    """raw_messenger/<방>/*.jsonl → stage/messenger/<방>/월별 대화록 md. 전체 재생성.
    방이 dept 하나뿐이면 기존처럼 stage/messenger/ 바로 아래에 둔다(하위호환)."""
    raw_root = PROJ / "output" / "raw_messenger"
    stage = PROJ / "output" / "stage" / "messenger"
    if stage.exists():
        for old in stage.rglob("*.md"):
            old.unlink()
    if not raw_root.exists():
        return 0
    rooms = sorted(d for d in raw_root.iterdir() if d.is_dir())
    flat = len(rooms) <= 1  # dept 단독이면 평면 유지
    total = 0
    for room in rooms:
        out_dir = stage if flat else stage / room.name
        out_dir.mkdir(parents=True, exist_ok=True)
        label = "부서방" if room.name == "dept" else room.name
        for f in sorted(room.glob("*.jsonl")):
            lines = [f"# {f.stem} {label} 대화록 (stage 자동 재생성 · 봇 발화 제외)", ""]
            cur_day = None
            for ln in f.read_text().splitlines():
                try:
                    m = json.loads(ln)
                except ValueError:
                    continue
                kst = m.get("kst", "")
                day, hm = kst[:10], kst[11:16]
                if day != cur_day:
                    lines.append(f"\n## {day}")
                    cur_day = day
                lines.append(f"- **{hm}** {m.get('sender','?')}: {m.get('text','')}")
                total += 1
            (out_dir / f"{f.stem}.md").write_text("\n".join(lines) + "\n")
    return total


def mail_accounts() -> list[str]:
    """config.json의 mail_accounts 배열. 없으면 {{BOT_USER}} 단일.
    2026-09-05 Roy 결정: 수집은 {{BOT_USER}} 만. research@ 그룹의 역할을 {{BOT_USER}} 이 대신한다.
    {{OPERATOR_USER}} 개인계정 경유는 폐기(토큰 8/12 사망 후 미복구, 폴백하면 조용히 0건 수집).
    계정 추가 절차: nwctl ua-auth <계정> (브라우저 동의 1회) → config에 등록."""
    try:
        cfg = json.loads((Path.home() / ".nworks" / "config.json").read_text())
        accounts = cfg.get("mail_accounts") or []
    except Exception:
        accounts = []
    return accounts or ["{{BOT_ACCOUNT}}"]


# 수집하지 않지만 **U 에 보관하는** 계열. 계정이 폐지돼도 그 코퍼스는 원본이다
# (Roy 2026-10-01: "6월부터의 이메일 파싱은 살려두어야 하고 U 에 저장되는 게 맞다").
# 수집 대상(mail_accounts)과 보관 계열은 다르다 — 섞으면 폐지 계정을 다시 긁는다.
FROZEN_SERIES = ["{{OPERATOR_USER}}"]          # received < T0(2026-08-12T15:29:53). 563건 고정


def archived_series() -> list[str]:
    """U 에 보관할 전 계열 키 = 수집 계열 + frozen 계열."""
    return [a.split("@")[0] for a in mail_accounts()] + FROZEN_SERIES


def main() -> None:
    _enable_dataless_materialization()
    log("cycle start (py)")
    # Roy 확정(2026-08-12): 주소 필터 없이 전 수신 메일 수집 (급여류 포함 — 기밀 아님)
    for acct in mail_accounts():
        key = acct.split("@")[0]
        run(f"pull[{key}]", [PY, str(PROJ / "scripts" / "nwctl"), "mail-pull", acct,
                             "--max", "200", "--filter", "none"])
        run(f"parse[{key}]", [PY, str(PROJ / "scripts" / "agents" / "mail_parser.py"), key])
    # 정리대장 — 업무에 필요한 메일만 SSOT 로 가도록 판정한다. 승격기(ssot_promote)가
    # 이것을 전제로 하고, 없으면 메일 승격을 멈춘다. 그래서 파싱 직후 매번 다시 만든다 —
    # 신규 메일이 대장에 없으면 "대장미등재"로 보류돼 조용히 밀린다.
    run("curate", [PY, str(PROJ / "scripts" / "agents" / "mail_curate.py")])
    run("calendar", [PY, str(PROJ / "scripts" / "agents" / "calendar_collector.py")])
    log(f"messenger stage: {build_messenger_stage()}건")

    if not (FOUNDRY / "00_raw").exists():
        log(f"sync SKIP: 드라이브 미마운트/권한 없음 ({FOUNDRY})")
        alert_roy(f"드라이브 미마운트/권한 없음 — 동기화 건너뜀\n{FOUNDRY}")
        return
    try:
        c1 = c2 = 0
        live = {a.split("@")[0] for a in mail_accounts()}
        for key in archived_series():
            raw_dst = stage_src = f"mail_{key}"
            c1 += sync_tree(PROJ / "output" / "raw_mail" / key, FOUNDRY / "00_raw" / raw_dst, mirror=False)
            # frozen 계열은 미러하지 않는다 — 로컬 코퍼스가 비면 U 원본이 지워진다.
            # 원본은 지우지 않는 쪽으로 틀린다.
            c2 += sync_tree(PROJ / "output" / "stage" / stage_src,
                            FOUNDRY / "10_stage" / stage_src, mirror=(key in live))
        # 대장과 SSOT 색인도 U 에 둔다 — 판정 근거가 원본 옆에 남아야 추적이 된다
        c2 += sync_tree(PROJ / "output" / "stage" / "mail_index",
                        FOUNDRY / "10_stage" / "mail_index", mirror=True)
        led = PROJ / "output" / "stage" / "mail_대장.tsv"
        if led.exists():
            dst = FOUNDRY / "10_stage" / "mail_대장.tsv"
            if (not dst.exists()) or dst.stat().st_size != led.stat().st_size:
                shutil.copy2(led, dst)
                c2 += 1
        c3 = sync_tree(PROJ / "output" / "raw_calendar", FOUNDRY / "00_raw" / "calendar", mirror=True)
        c4 = sync_tree(PROJ / "output" / "stage" / "calendar", FOUNDRY / "10_stage" / "calendar", mirror=True)
        c5 = sync_tree(PROJ / "output" / "raw_messenger", FOUNDRY / "00_raw" / "messenger", mirror=False)
        c6 = sync_tree(PROJ / "output" / "stage" / "messenger", FOUNDRY / "10_stage" / "messenger", mirror=True)
        log(f"sync done: mail_raw+{c1} mail_stage~{c2} cal_raw~{c3} cal_stage~{c4} msg_raw+{c5} msg_stage~{c6}")
        skipped = getattr(sync_tree, "skipped", 0)
        if skipped >= 5:
            alert_roy(f"동기화 중 파일 {skipped}건 건너뜀 — workspace/mail_cycle.log 확인 필요")
        sync_tree.skipped = 0
    except PermissionError as e:
        log(f"sync 권한 오류(TCC): {e}")
        alert_roy(f"드라이브 동기화 권한 오류(TCC): {e}")

    if LOG.stat().st_size > 1_000_000:
        LOG.write_text(LOG.read_text()[-500_000:])


if __name__ == "__main__":
    main()
