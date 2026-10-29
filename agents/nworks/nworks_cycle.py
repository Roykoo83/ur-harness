"""nworks cycle — mail pull -> parse -> calendar -> messenger -> drive sync (pure python).

주기는 launchd `com.{{ORG_NAME}}.nworks-mail` 의 StartInterval 이 정한다 (현재 3600초 = 1시간).
docstring 에 주기를 박아 두면 plist 를 고쳐도 문서만 낡는다 — plist 가 정본이다.

스케줄러가 이 파일을 돌린다. 파이썬은 설정(install.python) 또는 PATH 의 python3.
zsh+rsync 버전을 대체: launchd TCC(전체 디스크 접근)는 실행 바이너리 기준이므로
python3 단일 바이너리로 고정한다. U 로는 rclone(API)로 올린다 — 마운트를 쓰지 않는다.
- 00_raw: 추가/갱신만 (원본 불변 — 드라이브 쪽 삭제 없음)
- 10_stage: 미러 (로컬에 없는 파일은 드라이브에서도 삭제)
"""
import datetime
import json
import subprocess
import sys
from pathlib import Path

PROJ = Path(__file__).resolve().parents[2]
LOG = PROJ / "workspace" / "mail_cycle.log"
PY = sys.executable



def _send_kwargs() -> dict:
    """bot_send_text 에 넘길 수신자 인자. 대상이 없으면 빈 dict — 부르는 쪽이 건너뛴다."""
    tgt = _alert_target()
    if not tgt:
        return {}
    return {"user_id": tgt[1]} if tgt[0] == "user" else {"channel_id": tgt[1]}


def _alert_target():
    """알림 대상 → ("user"|"channel", id). 코드에 사람을 박지 않는다.

    설정의 `operator.account` 하나로 조직도를 조회해 1:1 대상을 얻고,
    못 찾으면 부서 채널로 돌린다. 둘 다 없으면 None — 조용히 사라지지 않게
    부르는 쪽이 로그에 남긴다.
    """
    import sys as _s
    from pathlib import Path as _P
    _s.path.insert(0, str(_P(__file__).resolve().parents[2] / "core"))
    from lib.identity import alert_target                        # noqa: PLC0415
    return alert_target()


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
# 401이 3회(=3시간) 연속되면 운영자 1:1 로 경고한다 — 승인된 발송 유형(사이클 이상 경고).
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
            sys.path.insert(0, str(PROJ / "agents"))
            from nworks.client import NworksClient
            from nworks.config import load_config
            from nworks.domains import bot_send_text
            bot_send_text(NworksClient(), load_config()["bot_id"],
                          f"⚠️ 부서 메일 수집이 {st['fails']}시간째 인증 실패(401)입니다.\n"
                          f"복구: HANDOVER.md 「복구 절차 R1」 (nwctl ua-auth {{BOT_USER}})\n"
                          f"(로그: nworks-agent/workspace/mail_cycle.log)",
                          user_id="{{OPERATOR_ACCOUNT}}")
            st["alerted_at"] = _t.time()
            log("UA 토큰 장애 경고 발송 (운영자 1:1)")
        except Exception as e:
            log(f"UA 장애 경고 실패: {str(e)[:120]}")
    _AUTH_STATE.write_text(_j.dumps(st))


def _enable_dataless_materialization() -> None:
    """launchd 프로세스는 기본적으로 클라우드 전용 파일 접근이 꺼져 있다(EDEADLK).
    IOPOL_TYPE_VFS_MATERIALIZE_DATALESS_FILES(3)을 ON(2)으로 켠다."""
    try:
        import ctypes
        libc = ctypes.CDLL("/usr/lib/libSystem.dylib")
        libc.setiopolicy_np(3, 0, 2)  # type, IOPOL_SCOPE_PROCESS, ON
    except Exception as e:
        log(f"materialize 정책 설정 실패(무시): {e}")


def alert_operator(msg: str) -> None:
    """사이클 이상 시 운영자 1:1 경고 (운영자 승인 발송 유형, 2026-08-05)."""
    try:
        sys.path.insert(0, str(PROJ / "agents"))
        from nworks.client import NworksClient
        from nworks.config import load_config
        from nworks.domains import bot_send_text
        bot_send_text(NworksClient(), load_config()["bot_id"],
                      f"[경고] 그룹웨어 사이클 이상\n{msg[:500]}", **_send_kwargs())
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
    2026-09-05 운영자 결정: 수집은 {{BOT_USER}} 만. research@ 그룹의 역할을 {{BOT_USER}} 이 대신한다.
    {{OPERATOR_USER}} 개인계정 경유는 폐기(토큰 8/12 사망 후 미복구, 폴백하면 조용히 0건 수집).
    계정 추가 절차: nwctl ua-auth <계정> (브라우저 동의 1회) → config에 등록."""
    try:
        cfg = json.loads((Path.home() / ".nworks" / "config.json").read_text())
        accounts = cfg.get("mail_accounts") or []
    except Exception:
        accounts = []
    return accounts or ["{{BOT_ACCOUNT}}"]


# 수집하지 않지만 **U 에 보관하는** 계열. 계정이 폐지돼도 그 코퍼스는 원본이다
# (운영자 2026-10-01: "6월부터의 이메일 파싱은 살려두어야 하고 U 에 저장되는 게 맞다").
# 수집 대상(mail_accounts)과 보관 계열은 다르다 — 섞으면 폐지 계정을 다시 긁는다.
FROZEN_SERIES = ["{{OPERATOR_USER}}"]          # received < T0(2026-08-12T15:29:53). 563건 고정


def archived_series() -> list[str]:
    """U 에 보관할 전 계열 키 = 수집 계열 + frozen 계열."""
    return [a.split("@")[0] for a in mail_accounts()] + FROZEN_SERIES


def main() -> None:
    _enable_dataless_materialization()
    log("cycle start (py)")
    # 운영자 확정(2026-08-12): 주소 필터 없이 전 수신 메일 수집 (급여류 포함 — 기밀 아님)
    for acct in mail_accounts():
        key = acct.split("@")[0]
        run(f"pull[{key}]", [PY, str(PROJ / "agents" / "nworks" / "nwctl"), "mail-pull", acct,
                             "--max", "200", "--filter", "none"])
        run(f"parse[{key}]", [PY, str(PROJ / "agents" / "nworks" / "mail_parser.py"), key])
    # 정리대장 — 업무에 필요한 메일만 SSOT 로 가도록 판정한다. 승격기(ssot_promote)가
    # 이것을 전제로 하고, 없으면 메일 승격을 멈춘다. 그래서 파싱 직후 매번 다시 만든다 —
    # 신규 메일이 대장에 없으면 "대장미등재"로 보류돼 조용히 밀린다.
    run("curate", [PY, str(PROJ / "agents" / "nworks" / "mail_curate.py")])
    run("calendar", [PY, str(PROJ / "agents" / "nworks" / "calendar_collector.py")])
    log(f"messenger stage: {build_messenger_stage()}건")

    # U 로는 **API 로** 올린다. 마운트는 이 기계에만 있고, 업로드가 서면
    # 올린 줄 알고 넘어간다 (2026-10-22 실측: 마운트 쓰기가 3시간 안 올라갔다)
    try:
        from lib import u_api                                  # noqa: PLC0415
        from lib.config import require                         # noqa: PLC0415
        U = require("u.paths.naverworks")
    except Exception as e:                                     # noqa: BLE001
        log(f"sync SKIP: U 를 API 로 열 수 없다 — {e}")
        alert_operator(f"U API 접근 실패 — 동기화 건너뜀\n{e}")
        return
    try:
        c1 = c2 = 0
        live = {a.split("@")[0] for a in mail_accounts()}
        for key in archived_series():
            raw_dst = stage_src = f"mail_{key}"
            if key not in live and not (PROJ / "output" / "raw_mail" / key).is_dir():
                # A frozen past series that is not on this machine: U already holds it.
                # Failing here blocked the ledger, calendar and messenger uploads after it.
                continue
            c1 += u_api.push(PROJ / "output" / "raw_mail" / key,
                             f"{U}/00_raw/{raw_dst}", mirror=False)
            # frozen 계열은 미러하지 않는다 — 로컬 코퍼스가 비면 U 원본이 지워진다.
            # 원본은 지우지 않는 쪽으로 틀린다.
            c2 += u_api.push(PROJ / "output" / "stage" / stage_src,
                             f"{U}/10_stage/{stage_src}", mirror=(key in live))
        # 대장과 SSOT 색인도 U 에 둔다 — 판정 근거가 원본 옆에 남아야 추적이 된다
        c2 += u_api.push(PROJ / "output" / "stage" / "mail_index",
                         f"{U}/10_stage/mail_index", mirror=True)
        led = PROJ / "output" / "stage" / "mail_대장.tsv"
        if led.exists():
            c2 += u_api.push(led.parent, f"{U}/10_stage",
                             mirror=False)
        c3 = u_api.push(PROJ / "output" / "raw_calendar", f"{U}/00_raw/calendar", mirror=True)
        c4 = u_api.push(PROJ / "output" / "stage" / "calendar", f"{U}/10_stage/calendar", mirror=True)
        c5 = u_api.push(PROJ / "output" / "raw_messenger", f"{U}/00_raw/messenger", mirror=False)
        c6 = u_api.push(PROJ / "output" / "stage" / "messenger", f"{U}/10_stage/messenger", mirror=True)
        log(f"sync done: mail_raw+{c1} mail_stage~{c2} cal_raw~{c3} cal_stage~{c4} msg_raw+{c5} msg_stage~{c6}")
    except u_api.UnavailableError as e:
        # 올리다 실패하면 **조용히 넘기지 않는다.** 올린 줄 알고 넘어가는 것이
        # 마운트 시절의 실패 양식이었다
        log(f"U 올리기 실패: {e}")
        alert_operator(f"U 올리기 실패 — 다음 회차에 재시도\n{e}")

    if LOG.stat().st_size > 1_000_000:
        LOG.write_text(LOG.read_text()[-500_000:])


if __name__ == "__main__":
    main()
