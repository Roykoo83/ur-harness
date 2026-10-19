#!/usr/bin/env python3
"""와치독 — 데이터취득계층_보완기획_260904.md §6-3

**성공 로그는 신선도를 증명하지 않는다.** nworks-brief 는 22일간 매일 "발송 완료" 를
찍었지만 소스는 8/12에 멈춰 있었다. rpbd-tasks 는 "메일 18건(최근 7일)" 을 보고했지만
그 '최근' 은 데이터 최댓값 기준이었다. 그래서 이 와치독은 로그를 읽지 않는다.

  ① 지연        하트비트 completed_at 나이 > expected_max_age
  ② 스키마      hb.validate() 위반 — '파일 존재' 를 정상으로 오인하지 않는다
  ③ cursor 단조 이전 실행보다 cursor 가 줄면 이상
  ④ 정합성      SSOT 감시 폴더의 API 건수 vs 로컬 마운트 건수 (드라이브 동기화 사고 탐지)
  ⑤ 생존·자원   상시 프로세스 PID · 디스크 여유 · load
  ⑥ 만료        토큰 만료 임박

경보는 기존 봇 발송 경로를 재사용하고, 발송 응답 id 를 receipt 로 남긴다 —
'수신' 이 사람의 확인이 아니라 기계 증거가 되도록.
6시간 중복 억제(notify.py 패턴). 심각도: 경고(1:1) / 정지(1:1 + Hermes).

사용: watchdog.py [--dry-run] [--json]
"""
from __future__ import annotations
import argparse, collections, unicodedata, json, os, re, shutil, subprocess, sys, time
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


SERVICE = "watchdog"
STATE = Path.home() / "{{WORKSPACE_NAME}}/state"
ALERTS = STATE / "alerts"
SUPPRESS_H = 6
NWORKS = _P("workspace")
MOUNT = Path("{{MOUNT_ROOT}}/공유 드라이브/{{SSOT_DRIVE}}")

# ④ 정합성 감시 폴더 — SSOT 안, API vs 마운트 대조
# 정합성 감시 대상 — 자동화가 쓰거나 직원이 읽는 곳 전부.
# 교차검증 A 지적: 2곳만 검사하면 "수렴" 주장의 범위가 너무 좁다.
CONSISTENCY = ["20_전략", "50_도구상자", "30_시스템/00_설계", "30_시스템/20_서비스",
               "{{DEPT_ROOT}}/50_대시보드", "{{DEPT_ROOT}}/20_예산",
               "{{DEPT_ROOT}}/00_부서정본/주간보고_초안", "_시스템/색인", "_시스템/보안"]

WATCHED = ["nworks-mail", "ssot-promote", "ssot-push", "u-changefeed", "ssot-index", "rpbd-tasks",
           "nworks-brief", "weekly-dashboard", "fathom-minutes", "hr-sync-check"]
LIVE_PROCS = {"nworks-bridge": "nworks_hermes_bridge.py", "nworks-tunnel": "cloudflared"}
TOKENS = {"ua_token_{{BOT_USER}}": Path.home() / ".nworks/ua_token_{{BOT_USER}}.json"}





def _alert_target():
    """알림 대상 → ("user"|"channel", id). 코드에 사람을 박지 않는다.

    설정의 `operator.account` 하나로 조직도를 조회해 1:1 대상을 얻고,
    못 찾으면 부서 채널로 돌린다. 둘 다 없으면 None — 조용히 사라지지 않게
    부르는 쪽이 로그에 남긴다.
    """
    import sys as _s
    from pathlib import Path as _P
    _s.path.insert(0, str(_P(__file__).resolve().parents[1] / "core"))
    from lib.identity import alert_target                        # noqa: PLC0415
    return alert_target()


def findings_delay_schema() -> list[dict]:
    out = []
    for name in WATCHED:
        d = hb.read(name)
        if d is None:
            out.append(dict(sev="경고", code="hb-missing", svc=name,
                            msg=f"{name}: 하트비트 없음 (아직 계측 미적용이거나 한 번도 성공 못 함)"))
            continue
        errs = hb.validate(d)
        if errs:
            out.append(dict(sev="정지", code="hb-schema", svc=name,
                            msg=f"{name}: 하트비트 스키마 위반 — {', '.join(errs)}"))
            continue
        if d["status"] == "no_evidence":
            out.append(dict(sev="경고", code="no-evidence", svc=name,
                            msg=f"{name}: 계측 실패(로그 파싱 불가) — 서비스 자체는 정상일 수 있음. "
                                f"{d.get('needs')}"))
            continue
        if d["status"] == "blocked":
            out.append(dict(sev="대기", code="blocked", svc=name,
                            msg=f"{name}: {d.get('reason')} → {d.get('needs','조치 대기')}"))
            continue
        age, cap = hb.age_min(d), d["expected_max_age_min"]
        if age > cap:
            sev = "정지" if age > cap * 3 else "경고"
            out.append(dict(sev=sev, code="stale", svc=name,
                            msg=f"{name}: {age:.0f}분 전 성공 (허용 {cap}분) · status={d['status']}"))
        elif d["status"] == "fail":
            out.append(dict(sev="경고", code="hb-fail", svc=name,
                            msg=f"{name}: 마지막 실행 실패 — {str(d.get('reason'))[:120]}"))
        elif d["status"] == "partial":
            out.append(dict(sev="경고", code="hb-partial", svc=name,
                            msg=f"{name}: 부분 성공 — {d.get('targets')}"))
    return out


def findings_cursor() -> list[dict]:
    """cursor 단조성 — 이전 관측을 별도 파일에 남겨 비교한다."""
    out, prev_p = [], STATE / "watchdog_cursors.json"
    try:
        prev = json.loads(prev_p.read_text())
    except (OSError, ValueError):
        prev = {}
    cur = {}
    for name in WATCHED:
        d = hb.read(name)
        v = (d or {}).get("input_cursor", {}).get("value")
        if isinstance(v, (int, float)):
            cur[name] = v
            if name in prev and v < prev[name]:
                out.append(dict(sev="경고", code="cursor-regress", svc=name,
                                msg=f"{name}: cursor 역행 {prev[name]} → {v}"))
    try:
        prev_p.write_text(json.dumps(cur, ensure_ascii=False))
    except OSError:
        pass
    return out


CHECK_TIMEOUT_S = 120     # rclone check 한 방향 상한. 9경로 실측 4~12s (2026-09-07)
PROBE_TIMEOUT_S = 20


def _mount_probe() -> str:
    """마운트가 응답하는지 먼저 본다. os.listdir 은 EDEADLK 에서 못 깨어나므로
    subprocess 로 격리해 타임아웃을 건다. 실패 문구를 돌려주고, 정상이면 빈 문자열."""
    try:
        p = subprocess.run(["ls", str(MOUNT)], capture_output=True, text=True,
                           timeout=PROBE_TIMEOUT_S)
    except subprocess.TimeoutExpired:
        return f"마운트 루트 ls {PROBE_TIMEOUT_S}s 무응답 — DriveFS 병증. 마운트 불신, 정합성 검사 생략"
    if p.returncode != 0:
        return f"마운트 루트 ls 실패 rc={p.returncode} — {p.stderr.strip()[-100:]}"
    return ""


def findings_consistency() -> list[dict]:
    """마운트 ↔ 클라우드 정합성.

    **건수 비교를 쓰지 않는다.** 2026-09-05 실측: 대량 쓰기 직후 Drive 목록 API 는
    최종적 일관성 상태라 같은 폴더를 `lsf -R`(170) · `lsf --fast-list`(126) ·
    `size`(123) 가 전부 다르게 세는 동안 `rclone check` 는 211 matching·0 differences 였다.
    건수로 판정하면 매 쓰기마다 거짓 경보가 난다.

    그래서 **해시 대조(rclone check)** 로 양방향을 본다:
      마운트→클라우드 누락 = 배포 안 된 것 (2026-09-05 174건이 이 상태였다)
      클라우드→마운트 누락 = 마운트가 최신을 못 보여주는 것 (KTL 사고)
    """
    out = []
    sick = _mount_probe()
    if sick:
        return [dict(sev="정지", code="mount-sick", svc="drive", msg=sick)]
    for rel in CONSISTENCY:
        d = MOUNT / rel
        if not d.is_dir():
            out.append(dict(sev="경고", code="mount-missing", svc="drive",
                            msg=f"{rel}: 마운트 경로 없음 (DriveFS 미동작?)"))
            continue
        for src, dst, label, code in (
                (str(d), f"SSOT_ro:{rel}", "클라우드 미배포", "push-lag"),
                (f"SSOT_ro:{rel}", str(d), "마운트 미반영", "mount-lag")):
            try:
                p = subprocess.run(["rclone", "check", src, dst, "--one-way", "--checksum",
                                    "--exclude", ".*"], capture_output=True, text=True,
                                   timeout=CHECK_TIMEOUT_S)
            except subprocess.TimeoutExpired:
                # 마운트 병증. 2026-09-07 ssot-push 가 EDEADLK 경로에서 5.7시간을 보냈다 —
                # 한 경로가 막히면 나머지도 막혀 있다고 보고 즉시 멈춘다. 체커 자신이
                # 마운트에 갇히면 아무것도 보고하지 못한다 (M 안 조건 ②).
                out.append(dict(sev="정지", code="mount-sick", svc="drive",
                                msg=f"{rel}: 마운트 응답 없음 {CHECK_TIMEOUT_S}s — 마운트 불신. 이후 경로 검사 중단"))
                return out
            except Exception as e:                          # noqa: BLE001
                out.append(dict(sev="경고", code="api-fail", svc="drive",
                                msg=f"{rel}: check {type(e).__name__}"))
                break
            if p.returncode == 0:
                continue
            err = (p.stderr or "")
            m = re.search(r"(\d+) differences found", err)
            miss = re.search(r"(\d+) (?:files )?missing", err)
            n = m.group(1) if m else (miss.group(1) if miss else "?")
            out.append(dict(sev="경고", code=code, svc="drive",
                            msg=f"{rel}: {label} {n}건 — DriveFS 동기화 지연"))
    return out


# 이름 끝이 " 2" 여도 중복이 아닌 것들 — 원래 파일명이다. 정본이 없으므로 자동
# 판정으로도 걸러지지만, 경보 목록에서까지 지우려면 명시해야 한다 (2026-09-07 실측).
DUP_ALLOW = frozenset({
    "{{DEPT_ROOT}}/10_과제/2_실주/중기부 글로벌협력형 R&D (카이저)/제출본/1. 연구개발계획서 _v1({{MEMBER_B}} 2.hwp",
    "{{DEPT_ROOT}}/10_과제/2_실주/중기부 글로벌협력형 R&D (카이저)/제출본/1. 연구개발계획서 _v1({{MEMBER_B}} 2.pdf",
    "{{DEPT_ROOT}}/10_과제/2_실주/노화·만성질환 공간전사체 DB/참고/"
    "공간바이오마커 기반 질병 데이터 구축 협력 위한 업무협약서(MOU) 2.pdf",
})
# 「이름 2」만 보면 「이름 3」을 놓친다 — 2026-09-07 오후 실측 3건. N=2..9 전부.
_DUP_RE = re.compile(r"^(?P<stem>.+) [2-9](?P<ext>\.[A-Za-z0-9]{1,8})?$")


def findings_dupes() -> list[dict]:
    """Drive `이름 2` 중복 사본 탐지 — 2026-09-07 사고 재발 방지.

    DriveFS 가 큐에 담아둔 사본과 rclone(API) 이 올린 정본이 Drive 안에 같은 이름으로
    공존하면 DriveFS 는 하나를 `이름 2` 로 렌더링하고, 그 순간부터 마운트의 해당
    경로는 `resource deadlock avoided`(EDEADLK) 를 던진다. 이것이 ssot-push 5경로를
    죽이고 fileproviderd 를 load 153 까지 몰아넣은 원인이었다.

    **건수가 아니라 이름으로 본다.** 중복은 목록 API 의 최종적 일관성과 무관하게
    이름으로 확정 판정되므로 findings_consistency 의 거짓경보 문제를 겪지 않는다.
    """
    out = []
    try:
        p = subprocess.run(["rclone", "lsf", "-R", "--files-only", "--format", "ph",
                            "--hash", "MD5", "--fast-list", "SSOT_ro:"],
                           capture_output=True, text=True, timeout=600)
    except Exception as e:                                  # noqa: BLE001
        return [dict(sev="경고", code="dupe-scan-fail", svc="drive",
                     msg=f"중복 스캔 실패: {type(e).__name__}")]
    if p.returncode != 0:
        return [dict(sev="경고", code="dupe-scan-fail", svc="drive",
                     msg=f"중복 스캔 rc={p.returncode}")]

    H, seen = {}, collections.defaultdict(list)
    for line in p.stdout.splitlines():
        if ";" in line:
            path, md5 = line.rsplit(";", 1)
            k = unicodedata.normalize("NFC", path)
            H[k] = md5
            seen[k].append(md5)

    # ── Drive 동명 파일 — API 에서만 보이는 중복 ──────────────────────────
    # 같은 폴더에 같은 이름이 둘. DriveFS 는 하나를 「이름 2」로 보여주지만 API 목록에는
    # 이름 그대로 두 번 나온다. 2026-09-07 오전 정리는 「 N」접미만 봐서 이 계층 9경로를
    # 통째로 놓쳤다. 원인은 rpbd-tasks 가 같은 파일을 마운트+API 로 같은 초에 두 번 올린 것.
    sn_same, sn_div = 0, []
    for k, hs in seen.items():
        if len(hs) < 2:
            continue
        if len(set(hs)) == 1:
            sn_same += 1
        else:
            sn_div.append(k)
    for k in sn_div:
        out.append(dict(sev="정지", code="dupe-samename-divergent", svc="drive",
                        msg=f"{k}: 같은 이름 {len(seen[k])}개, 내용 다름 — 어느 것이 정본인지 사람이 판단"))
    if sn_same:
        out.append(dict(sev="경고", code="dupe-samename", svc="drive",
                        msg=f"Drive 동명 파일 {sn_same}경로 (내용 동일) — `drive_dedupe.py --apply` (rclone dedupe)"))

    same = 0
    for f in sorted(H):
        if f in DUP_ALLOW:
            continue
        head, base = f.rsplit("/", 1) if "/" in f else ("", f)
        m = _DUP_RE.match(base)
        if not m:
            continue
        canon = (head + "/" if head else "") + m["stem"] + (m["ext"] or "")
        if canon not in H:
            continue                       # 정본 없음 → 원래 파일명
        if H[canon] == H[f]:
            same += 1                      # 잉여 사본 — 묶어서 한 줄로 보고
        else:
            # 내용이 갈렸다. 동료가 낡은 쪽을 열 수 있으므로 자동 삭제 불가.
            out.append(dict(sev="정지", code="dupe-divergent", svc="drive",
                            msg=f"{f}: 정본과 내용 다른 중복 사본 — 어느 쪽이 정본인지 사람이 판단해야 함"))
    if same:
        out.append(dict(sev="경고", code="dupe-redundant", svc="drive",
                        msg=f"Drive 중복 사본 {same}건 (내용 동일) — 마운트 EDEADLK 유발. "
                            f"`scripts/drive_dedupe.py --apply` 로 정리"))
    return out


# DriveFS 충돌 렌더 세 꼴: 「 2.html」 「 2 (1).html」 「 (2).png」 — 2026-09-08 실측 전부 나왔다
_JUNK_RE = re.compile(r"(?: [2-9](?: \(\d+\))?| \(\d+\))\.[A-Za-z0-9]{1,8}$")


def findings_mount_junk() -> list[dict]:
    """기계 구역 폴더의 **마운트 쪽** 충돌 잔재 — DriveFS 재업로드 고리의 원천.

    2026-09-08 실측: 클라우드에서 「 2」「 3」을 지워도 운영자 마운트에 남은 로컬 사본(`… 2.html`,
    `… 2 (1).html`)을 DriveFS 가 "클라우드에 없는 로컬 파일"로 보고 원래 mtime 그대로 다시 올렸다.
    API 쪽 dedupe 만으로는 끝나지 않는다 — 마운트 쪽을 지워야 고리가 끊긴다.
    기계 구역은 API 가 쓰는 곳이므로, 마운트에 「 N」 이름이 보이면 그것은 예외 없이 잔재다.
    """
    out = []
    try:
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        from lib.ssot_io import in_machine_zone                 # noqa: PLC0415
    except Exception:                                           # noqa: BLE001
        return out
    for rel in CONSISTENCY:
        if not in_machine_zone(rel + "/"):
            continue
        try:
            p = subprocess.run(["ls", "-1R", str(MOUNT / rel)], capture_output=True,
                               text=True, timeout=PROBE_TIMEOUT_S)
        except subprocess.TimeoutExpired:
            continue                                             # 병증은 consistency 가 잡는다
        junk = [ln for ln in p.stdout.splitlines()
                if _JUNK_RE.search(unicodedata.normalize("NFC", ln))]
        if junk:
            out.append(dict(sev="정지", code="mount-junk", svc="drive",
                            msg=f"{rel}: 마운트에 충돌 잔재 {len(junk)}건 ({junk[0][-40:]} …) — "
                                f"기계 구역이므로 마운트에서 rm 해야 DriveFS 재업로드가 멈춘다"))
    return out


def findings_live() -> list[dict]:
    out = []
    for name, pat in LIVE_PROCS.items():
        r = subprocess.run(["pgrep", "-f", pat], capture_output=True, text=True)
        if r.returncode != 0:
            out.append(dict(sev="정지", code="proc-down", svc=name, msg=f"{name}: 프로세스 없음 ({pat})"))
    free_gb = shutil.disk_usage("/").free / 2**30
    if free_gb < 20:
        out.append(dict(sev="정지" if free_gb < 10 else "경고", code="disk", svc="host",
                        msg=f"디스크 여유 {free_gb:.0f}GB"))
    try:
        la = os.getloadavg()[1]
        if la > 15:
            out.append(dict(sev="경고", code="load", svc="host", msg=f"load(5m) {la:.0f}"))
    except OSError:
        pass
    return out


def findings_expiry() -> list[dict]:
    out = []
    for name, p in TOKENS.items():
        if not p.exists():
            out.append(dict(sev="경고", code="token-missing", svc=name, msg=f"{name}: 파일 없음"))
            continue
        age_d = (time.time() - p.stat().st_mtime) / 86400
        if age_d > 83:                          # UA 토큰 90일
            out.append(dict(sev="경고", code="token-expiry", svc=name,
                            msg=f"{name}: 발급 {age_d:.0f}일 경과 (90일 만료 · 갱신 절차 HANDOVER.md R1)"))
    sa = Path.home() / ".rpb/sa_{{WORKSPACE_SLUG}}.json"
    if not sa.exists():
        out.append(dict(sev="정지", code="sa-missing", svc="drive",
                        msg="서비스계정 키 없음 — SSOT API 접근 불가"))
    return out


def send(text: str, hard: bool, dry: bool) -> dict:
    if dry:
        return {"sent": False, "dry": True}
    rec = {"sent": False}
    try:
        sys.path.insert(0, str(NWORKS / "scripts"))
        from nworks.client import NworksClient          # noqa: PLC0415
        from nworks.config import load_config           # noqa: PLC0415
        from nworks.domains import bot_send_text        # noqa: PLC0415
        cfg = load_config()
        tgt = _alert_target()
        if not tgt:
            log("알림 대상을 찾지 못해 보내지 않는다 — operator.account 를 확인한다")
            return
        kw = {"user_id": tgt[1]} if tgt[0] == "user" else {"channel_id": tgt[1]}
        r = bot_send_text(NworksClient(cfg), cfg["bot_id"], text[:900], **kw)
        rec.update(sent=True, message_id=str(r)[:120] if r else None)
    except Exception as e:                              # noqa: BLE001
        rec["error"] = f"{type(e).__name__}: {e}"[:200]
    if hard:
        try:
            env = {**os.environ, "HERMES_HOME": str(Path.home() / ".hermes/profiles/be_{{ORG_NAME}}")}
            subprocess.run(["hermes", "send", "-t", "discord:rojdoi", "-q",
                            "-s", "{{ORG_NAME}} AX 와치독 · 정지", text[:1500]],
                           env=env, capture_output=True, timeout=60)
            rec["hermes"] = True
        except Exception:                               # noqa: BLE001
            rec["hermes"] = False
    return rec


def board(findings: list[dict], dry: bool) -> str:
    """상태 보드 — SSOT `_시스템/상태/health.md`. 부서원이 '지금 데이터가 살아있나'를 스스로 본다."""
    icon = {"ok": "🟢", "partial": "🟡", "blocked": "🔵", "no_evidence": "⚪", "fail": "🔴"}
    rows = []
    for name in WATCHED:
        d = hb.read(name)
        if d is None:
            rows.append((name, "⚪", "미계측", "-", "-")); continue
        a = hb.age_min(d)
        st = d.get("status", "?")
        stale = a is not None and a > d.get("expected_max_age_min", 120)
        rows.append((name, "🔴" if stale else icon.get(st, "⚪"),
                     "지연" if stale else {"ok": "정상", "partial": "부분", "blocked": "대기", "no_evidence": "계측실패", "fail": "실패"}.get(st, st),
                     f"{a:.0f}분 전" if a is not None else "-",
                     str(d.get("input_cursor", {}).get("value", "-"))))
    hard = [x for x in findings if x["sev"] == "정지"]
    warn = [x for x in findings if x["sev"] == "경고"]
    wait = [x for x in findings if x["sev"] == "대기"]
    head = "🔴 정지 있음" if hard else ("🟡 경고 있음" if warn else "🟢 정상")
    L = ["# 시스템 상태", "",
         f"> {time.strftime('%Y-%m-%d %H:%M')} 자동 갱신 (15분 주기 · `com.{{ORG_NAME}}.watchdog`) — **{head}**",
         "> 자동화가 멈추면 여기 색이 바뀌고 운영자에게 알림이 갑니다. 이 파일은 편집하지 마세요.", "",
         "## 데이터 수집·배포", "", "| 서비스 | 상태 | 마지막 성공 | cursor |", "|---|---|---|---|"]
    label = {"nworks-mail": "메일·캘린더·부서방 수집", "ssot-promote": "SSOT 승격",
             "ssot-push": "마운트→클라우드 배포",
             "u-changefeed": "U 변경 추적", "ssot-index": "SSOT 색인",
             "rpbd-tasks": "운영OS 원장", "nworks-brief": "일일 브리핑",
             "weekly-dashboard": "주간 대시보드", "fathom-minutes": "회의록 수집",
             "hr-sync-check": "인사 동기화 점검"}
    for n, ic, st, age, cur in rows:
        L.append(f"| {label.get(n, n)} (`{n}`) | {ic} {st} | {age} | {cur} |")
    if findings:
        L += ["", "## 지금 걸린 것", ""]
        for x in hard + warn + wait:
            L.append(f"- **[{x['sev']}]** {x['msg']}")
    else:
        L += ["", "## 지금 걸린 것", "", "없음"]
    L += ["", "---",
          "- 🟢 정상 · 🟡 부분 · 🔵 조치 대기(알려진 차단) · ⚪ 계측실패(서비스는 정상일 수 있음) · 🔴 실패·지연",
          "- 판정은 로그가 아니라 **하트비트 나이와 입력 cursor** 로 합니다 — "
          "성공 로그는 신선도를 증명하지 않습니다(2026-09-04, 22일 무음 사고).",
          "- 근거: `30_시스템/00_설계/데이터취득계층_보완기획_260904.md` §6"]
    body = "\n".join(L) + "\n"
    local = STATE / "health.md"
    local.write_text(body, encoding="utf-8")
    if not dry:
        subprocess.run(["rclone", "copyto", str(local), "SSOT_rw:_시스템/상태/health.md"],
                       capture_output=True, timeout=120)
    return head


def _write_canary(findings: list[dict], head: str) -> None:
    """신선도 표지 — M 안 조건 ③.

    체커(이 프로그램)는 **운영자 의 마운트만** 증명한다. 직원 마운트는 각자 다르다. 그래서
    클라우드에 15분마다 갱신되는 파일 하나를 두고, 어느 마운트에서든 이 파일의
    generated 가 오래돼 보이면 "내 마운트가 뒤처졌다"를 그 자리에서 알 수 있게 한다.
    설치도 크레덴셜도 필요 없다 — 지침 한 줄이면 된다. API 로만 쓴다 (기계 구역).
    """
    idx = hb.read("ssot-index") or {}
    codes = collections.Counter(x["code"] for x in findings)
    body = {
        "schema_version": "canary/1.0",
        "generated": time.strftime("%Y-%m-%dT%H:%M:%S+0900"),
        "watchdog_run": os.getpid(),
        "board": head,
        "mount_health": "sick" if codes.get("mount-sick") else "ok",
        "mount_lag_paths": codes.get("mount-lag", 0),
        "push_lag_paths": codes.get("push-lag", 0),
        "dupes": {"suffix": codes.get("dupe-redundant", 0) + codes.get("dupe-divergent", 0),
                  "samename": codes.get("dupe-samename", 0) + codes.get("dupe-samename-divergent", 0)},
        "index_generated": idx.get("completed_at"),
        "index_status": idx.get("status"),
        "how_to_read": "이 파일의 generated 가 지금보다 30분 이상 오래됐으면 당신의 마운트가 뒤처진 것이다. "
                       "index_generated 가 4시간 넘었으면 색인이 놓친 파일이 있을 수 있다.",
    }
    tmp = STATE / "canary.json"
    tmp.write_text(json.dumps(body, ensure_ascii=False, indent=1), encoding="utf-8")
    try:
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        from lib.ssot_io import put                          # noqa: PLC0415
        ok, msg = put(tmp, "_시스템/상태/canary.json")
        if not ok:
            print(f"  ! canary 미반영: {msg}")
    except Exception as e:                                   # noqa: BLE001
        print(f"  ! canary 실패: {type(e).__name__}: {e}")


def _run() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()

    f = (findings_delay_schema() + findings_cursor() + findings_consistency()
         + findings_mount_junk() + findings_live() + findings_expiry() + findings_dupes())
    hard = [x for x in f if x["sev"] == "정지"]
    f_alert = [x for x in f if x["sev"] != "대기"]   # 대기는 경보 대상이 아니다

    if a.json:
        print(json.dumps(f, ensure_ascii=False, indent=1))
    else:
        wait = len(f) - len(f_alert)
        print(f"검사 완료 · 이상 {len(f_alert)}건 (정지 {len(hard)} · 경고 {len(f_alert)-len(hard)})"
              + (f" · 대기 {wait}건" if wait else ""))
        for x in f:
            print(f"  [{x['sev']}] {x['msg']}")
        if not f:
            print("  이상 없음")

    # 중복 억제 후 발송
    ALERTS.mkdir(parents=True, exist_ok=True)
    sup = ALERTS / "_suppress.json"
    try:
        seen = json.loads(sup.read_text())
    except (OSError, ValueError):
        seen = {}
    now, fresh = time.time(), []
    for x in f_alert:
        k = f"{x['code']}:{x['svc']}"
        if now - seen.get(k, 0) > SUPPRESS_H * 3600:
            fresh.append(x); seen[k] = now
    if fresh and not a.dry_run:
        body = "[{{ORG_NAME}} AX 와치독]\n" + "\n".join(f"[{x['sev']}] {x['msg']}" for x in fresh)
        rec = send(body, any(x["sev"] == "정지" for x in fresh), a.dry_run)
        aid = time.strftime("%Y%m%dT%H%M%S")
        (ALERTS / f"{aid}.json").write_text(json.dumps(
            {"alert_id": aid, "run_id": os.getpid(), "findings": fresh, "receipt": rec},
            ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"  → 경보 {len(fresh)}건 발송 (receipt {aid}.json · sent={rec.get('sent')})")
        try:
            sup.write_text(json.dumps(seen, ensure_ascii=False))
        except OSError:
            pass
    elif fresh:
        print(f"  → [dry-run] 경보 {len(fresh)}건 발송 예정")

    head = board(f, a.dry_run)
    print(f"  보드: {head} → SSOT/_시스템/상태/health.md")
    if not a.dry_run:
        _write_canary(f, head)
    hb.ok(SERVICE, counters={"findings": len(f), "hard": len(hard)},
          outputs=[{"path": str(ALERTS), "count": len(fresh)}])
    return 0


def main() -> int:
    """자기 실패를 반드시 하트비트에 남긴다.

    와치독이 중간에 죽으면 과거 hb 만 남아 '정상'으로 보인다.
    핸들러를 `__main__` 이 아니라 **여기** 두는 이유: import 해서 main() 을 부르는 경로
    (테스트·다른 스크립트)에서도 동작해야 한다. `__main__` 에만 두면 그 경로를 놓친다
    (교차검증 2026-09-05 B② → 1차 수정이 이 이유로 무효였음, 재수정).
    """
    try:
        return _run()
    except BaseException as e:                           # noqa: BLE001
        import traceback
        try:
            hb.fail(SERVICE, f"{type(e).__name__}: {e}")
        finally:
            traceback.print_exc()
        return 3


if __name__ == "__main__":
    raise SystemExit(main())
