#!/usr/bin/env python3
"""게이트 판정 하네스 — 보완기획 §7 W3 (판정을 코드로)

기획서 게이트를 사람이 눈으로 확인하면 "통과했다"가 주장이 된다.
이 스크립트가 exit 0 이면 통과, 아니면 미통과다. 항목별 근거를 함께 출력한다.

사용: gate_check.py [--phase W1|W2|W3|all] [--json]
"""
from __future__ import annotations
import argparse, json, os, re, shutil, subprocess, sys, time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from lib import hb
from lib.ssot_io import count as ssot_count, listing as ssot_list

REPO = Path.home() / "{{WORKSPACE_NAME}}"
HARNESS = Path(__file__).resolve().parents[1]   # 저장소 루트 — 코드는 여기 있다
MOUNT = Path("{{MOUNT_ROOT}}/공유 드라이브/{{SSOT_DRIVE}}")
SERVICES = ["nworks-mail", "ssot-promote", "u-changefeed", "ssot-index", "rpbd-tasks",
            "nworks-brief", "weekly-dashboard", "fathom-minutes", "hr-sync-check", "watchdog"]
R = []



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


def _mail_curate():
    """접두·계열 경계의 소유자. 세 곳에 따로 적으면 어긋나고,
    어긋나면 게이트가 정상인 것을 위반으로 보고한다."""
    import sys as _s
    d = str(HARNESS / "agents" / "nworks")
    if d not in _s.path:
        _s.path.insert(0, d)
    import mail_curate                                        # noqa: PLC0415
    return mail_curate


def _mail_key_re() -> str:
    """파일명에서 **대장 키**(`bot-1160`)를 뽑는 식. 뒤의 `_` 는 포함하지 않는다."""
    pres = [pr.rstrip("-") for _st, pr in _mail_curate().SERIES.values()]
    return r"/((?:" + "|".join(sorted(set(pres))) + r")-\d+)_"


def _mail_prefix_re() -> str:
    pres = [pr.rstrip("-") for _st, pr in _mail_curate().SERIES.values()]
    return r"/(?:" + "|".join(sorted(set(pres))) + r")-\d+_"


def _series_windows() -> dict:
    return getattr(_mail_curate(), "SERIES_WINDOW", {})


def chk(phase: str, name: str, ok: bool | None, detail: str) -> None:
    R.append({"phase": phase, "check": name,
              "result": "PASS" if ok else ("BLOCKED" if ok is None else "FAIL"),
              "detail": detail})


# ── W1 ────────────────────────────────────────────────────────────────
def w1() -> None:
    sa = Path.home() / ".rpb/sa_{{WORKSPACE_SLUG}}.json"
    chk("W1", "서비스계정 키 존재", sa.exists() and oct(sa.stat().st_mode)[-3:] == "600",
        f"{sa} · 권한 {oct(sa.stat().st_mode)[-3:] if sa.exists() else '없음'}")
    try:
        out = subprocess.run(["rclone", "listremotes"], capture_output=True, text=True, timeout=30).stdout
    except Exception:                                        # noqa: BLE001
        out = ""
    for r in ("SSOT_ro:", "SSOT_rw:", "U_ro:"):
        chk("W1", f"원격 {r} 설정", r in out, "rclone listremotes")
    # 읽기전용 격리 — 실제로 쓰기가 막히는지
    t = Path(subprocess.run(["mktemp", "-d"], capture_output=True, text=True).stdout.strip()) / "x.txt"
    t.parent.mkdir(parents=True, exist_ok=True); t.write_text("gate")
    p = subprocess.run(["rclone", "copyto", str(t), "SSOT_ro:_시스템/상태/_gate_ro.txt"],
                       capture_output=True, text=True, timeout=90)
    chk("W1", "읽기전용 원격 쓰기 차단", p.returncode != 0, f"rc={p.returncode} (0이면 격리 실패)")
    shutil.rmtree(t.parent, ignore_errors=True)
    # 하트비트 스키마
    bad = []
    for s in SERVICES:
        d = hb.read(s)
        if d is None:
            bad.append(f"{s}:없음"); continue
        e = hb.validate(d)
        if e:
            bad.append(f"{s}:{e[0]}")
    chk("W1", f"하트비트 {len(SERVICES)}개 스키마 검증", not bad, "; ".join(bad) or "전부 통과")


# ── W2 ────────────────────────────────────────────────────────────────
def w2() -> None:
    # 승격 목적지 검증 + cursor
    d = hb.read("ssot-promote")
    chk("W2", "ssot-promote 성공 상태", bool(d) and d.get("status") == "ok",
        f"status={d.get('status') if d else '없음'} · cursor={(d or {}).get('input_cursor', {}).get('value')}")
    # ── 메일 승격은 **대장** 이 정한다. stage 전량이 아니다 (2026-10-01).
    # 예전 판정식(stage 전량 − S3)은 대장 필터가 켜진 뒤 항상 실패한다 — 분모가 틀렸다.
    import csv as _csv
    led_p = _P("stage", "mail_대장.tsv")
    led = []
    if led_p.exists():
        with led_p.open(encoding="utf-8") as fh:
            led = list(_csv.DictReader(fh, delimiter="\t"))
    chk("W2", "정리대장 존재", bool(led),
        f"{len(led)}행 · {led_p.name}" if led else "없음 — 승격이 blocked 된다")
    if led:
        wantY = {r["ssot_name"] for r in led if r["promote"] == "Y"}
        wantN = {r["ssot_name"] for r in led if r["promote"] == "N"}
        api_files = ssot_list("{{DEPT_ROOT}}/40_커뮤니케이션/메일")
        if api_files is None:
            chk("W2", "SSOT 메일 = 대장 승격분", False, "API 목록 조회 실패")
        else:
            import re as _re
            have = {m.group(1) for f in api_files
                    if (m := _re.search(_mail_key_re(), "/" + f))}
            missing, leaked = wantY - have, wantN & have
            orphan = have - wantY - wantN
            # S3(본문에 실제 계좌·주민번호)는 설계상 제외 — 소수의 missing 은 허용
            chk("W2", "업무무관 메일이 SSOT 에 없다", not leaked,
                f"누출 {len(leaked)}건" + (f": {sorted(leaked)[:3]}" if leaked else " (0)"))
            chk("W2", "SSOT 메일이 전부 대장에 있다", not orphan,
                f"고아 {len(orphan)}건" + (f": {sorted(orphan)[:3]}" if orphan else " (0)"))
            chk("W2", "대장 승격분이 SSOT 에 도착", len(missing) <= 10,
                f"API {len(have)} · 대장Y {len(wantY)} · 미도착 {len(missing)}"
                + (f" {sorted(missing)[:3]}" if missing else "") + " (S3제외·직전주기 허용 10)")
            # 접두 없는 메일 파일은 존재하지 않아야 한다 — 폐지된 research 계열의 흔적
            noprefix = [f for f in api_files if f.endswith(".md")
                        and "/" in f and not _re.search(_mail_prefix_re(), f)]
            chk("W2", "접두 없는 메일 파일 0건", not noprefix,
                f"{len(noprefix)}건" + (f": {noprefix[:2]}" if noprefix else ""))
        # 계열 경계 — frozen 이 늘거나 live 가 T0 이전을 받으면 분리가 깨진 것
        # 경계는 mail_curate.SERIES_WINDOW 가 소유한다 — 여기에 또 적지 않는다.
        # 계열이 늘 때(2026-10-21 봇 계열 추가) 하나의 T0 로 재면 거짓 위반이 난다
        _w = _series_windows()
        bad = []
        for r in led:
            lo, hi = _w.get(r["series"], (None, None))
            rt = r["received"]
            if rt and ((lo and rt < lo) or (hi and rt > hi)):
                bad.append(r)
        froz = sum(1 for r in led if r["series"] == "{{OPERATOR_USER}}")
        chk("W2", "계열 경계 무결", not bad,
            f"frozen {froz} · 경계위반 {len(bad)}")
    # 메일 단일화 — research 분기 잔존 0
    hits = []
    # mail_collector.py 가 빠져 있어 거기서만 research 분기가 살아남았다 (2026-09-28 발견).
    # 감시 목록이 좁으면 게이트는 통과하면서 잔재는 남는다.
    for f in ("agents/nworks/nworks_cycle.py",
              "agents/nworks/mail_parser.py",
              "agents/nworks/mail_collector.py"):
        t = (REPO / f).read_text(encoding="utf-8", errors="ignore")
        if '"research" if' in t or 'else "research"' in t:
            hits.append(f)
    chk("W2", "research 분기 제거", not hits, "; ".join(hits) or "잔존 0")
    # 폐지된 **계정키 없는** 메일 경로가 코드에 다시 등장하면 잡는다.
    # 문서는 고쳐도 코드에 리터럴이 남으면 그 폴더가 되살아난다 (2026-09-29 교훈).
    # 패턴을 **조립해서** 만든다. 소스에 그대로 적으면 이 파일 자신이 걸려
    # 가드가 영원히 FAIL 한다 (2026-10-01 실측 — 자기 참조).
    _bad = [pre + "/mail" + "/" for pre in ("00_raw", "10_stage", "stage")]
    _bad.append("raw_mail" + "/research")
    DEAD = [q + b + q for b in _bad for q in ('"', "'")]
    lit = []
    targets = sorted((HARNESS / "agents").rglob("*.py"))
    targets += [HARNESS / "core" / f for f in ("ssot_promote.py", "watchdog.py")]
    for f in targets:
        if not f.exists():
            continue
        t = f.read_text(encoding="utf-8", errors="ignore")
        for d in DEAD:
            if d in t:
                lit.append(f"{f.name}:{d}")
    chk("W2", "폐지 메일경로 리터럴 0건", not lit, "; ".join(lit[:3]) or "잔존 0")
    chk("W2", "{{OPERATOR_USER}} 토큰 폐기", not (Path.home() / ".nworks/ua_token_{{OPERATOR_USER}}.json").exists(),
        "~/.nworks/ua_token_{{OPERATOR_USER}}.json")
    # 소비자 문법 검증
    bad = []
    for f in ("daily_brief.py", "mail_parser.py", "nworks_cycle.py", "nworks_hermes_bridge.py",
              "mail_collector.py"):
        p = HARNESS / "agents" / "nworks" / f
        if p.exists():
            r = subprocess.run([sys.executable, "-m", "py_compile", str(p)], capture_output=True)
            if r.returncode != 0:
                bad.append(f)
    chk("W2", "소비자 5개 문법(fixture)", not bad, "; ".join(bad) or "전부 컴파일 통과")
    # 쓰기 경로 — SSOT_rw 사용 여부
    legacy = []
    for f in ("core/publish_dashboards.py", "tasks/tasks/make_weekly_pptx.py"):
        t = (REPO / f).read_text(encoding="utf-8", errors="ignore")
        if "gdrive,team_drive" in t and "폴백" not in t.split("gdrive,team_drive")[0][-80:]:
            legacy.append(f)
    chk("W2", "SSOT 쓰기 경로 서비스계정 전환", not legacy, "; ".join(legacy) or "gdrive 직접사용 0")


# ── W3 ────────────────────────────────────────────────────────────────
def w3() -> None:
    # 마운트 경로 참조 (U 읽기는 SA 권한 없어 불가 → BLOCKED 로 구분)
    # ── 마운트 참조 판정을 다시 정의한다 (2026-09-05) ───────────────────────
    # 기획서 원안은 "grep CloudStorage = 0건" 이었다. 실측 후 **달성해서도 안 되는 게이트**로 판단한다:
    #   ① 와치독·게이트·push 는 마운트를 '대조·보정 대상' 으로 읽는 것이 목적이다 — 없애면 기능이 사라진다
    #   ② hr_sync_check 는 주 1회 컨플루언스 아카이브 4,147건을 훑는다. API 로 바꾸면 훨씬 느리고
    #      새 실패면(쿼터·타임아웃)이 생긴다. 마운트 읽기가 합리적인 곳이다
    #   ③ Drive 목록 API 는 대량 쓰기 직후 최종적 일관성 상태다(같은 폴더를 170/126/123 으로 셌다)
    # 그래서 진짜 안전 속성으로 바꿔 잰다: **마운트와 클라우드가 수렴하고, 벌어지면 탐지된다.**
    ALLOW = {  # 마운트 참조가 설계상 정당한 파일 (이유를 코드에 남긴다)
        "watchdog.py": "④ 정합성 검사가 마운트를 대조 대상으로 읽는다",
        "gate_check.py": "이 판정기 자신",
        "ssot_push.py": "마운트를 읽어 클라우드로 올리던 보정기 — 퇴역(2026-09-07), 수렴 검사가 대신한다",
        "nworks_hermes_bridge.py": "프롬프트 문자열(경로 안내) — 코드가 읽지 않는다",
        "new-project.py": "사람이 1회 실행하는 개설 도구 (launchd 아님)",
        "hr_sync_check.py": "주 1회 아카이브 4,147건 훑기 — API 전환이 더 위험",
        "publish_dashboards.py": "로컬 미러 유지 + 업로드는 API(cloud_put→ssot_io)",
        "sync_budget_status.py": "마운트 쓰기 — ssot-push 퇴역(2026-09-07) 후 DriveFS 가 올린다",
        "build_budget_dataset.py": "마운트 쓰기 — ssot-push 퇴역(2026-09-07) 후 DriveFS 가 올린다",
        "make_weekly_pptx.py": "로컬 경로 상수 · 업로드는 SSOT_rw",
        "extract_observed.py": "SSOT 산출물 관측 — 읽기만 한다",
        "roster_sync.py": "네이버웍스 조직도를 온톨로지 `사람/`에 반영 — 마운트 쓰기, DriveFS 가 올린다",
        "drivefs_resync.py": "마운트 자체가 대상인 복구 도구 (사람이 승인 후 1회 실행)",
        "ledger_rebuild.py": "보안대장 재등재 — SSOT 본문을 읽어 등급을 매긴다 (사람 실행)",
        "build_id_sheet.py": "과제 폴더의 샘플 ID 현황표 생성 (사람 실행)",
        "온톨로지수확.py": "온톨로지 성장분 **제안**만 만든다 — 쓰지 않는다 (사람 실행)",
        "원장생성.py": "운영 OS 원장 회차 생성 — 마운트 쓰기 (사람 실행)",
    }
    CODE_EXT = (".py", ".sh")
    try:
        r = subprocess.run(["grep", "-rl", "CloudStorage/GoogleDrive", str(HARNESS / "core"),
                            str(REPO / "projects")], capture_output=True, text=True, timeout=120)
        raw = [x for x in r.stdout.splitlines() if x.strip()]
    except Exception:                                        # noqa: BLE001
        raw = []
    code = [x for x in raw if x.endswith(CODE_EXT) and "/node_modules/" not in x
            and "/.pytest_cache/" not in x and "/inbox/" not in x and "/_archive/" not in x]
    def has(f, pat):
        try:
            return pat in Path(f).read_text(encoding="utf-8", errors="ignore")
        except OSError:
            return False
    u_ref = [f for f in code if has(f, "공유 드라이브/U/")]
    undoc = [f for f in code if has(f, "{{SSOT_DRIVE}}") and Path(f).name not in ALLOW]
    chk("W3", "마운트 참조가 전부 문서화된 예외인가", not undoc,
        f"미문서화 {len(undoc)}: " + ("; ".join(Path(f).name for f in undoc) or "0")
        + f" · 문서화된 예외 {len(ALLOW)}개")
    chk("W3", "U 마운트 참조", None if u_ref else True,
        f"{len(u_ref)}곳 — U 서비스계정 권한 대기(BLOCKED)")
    # 진짜 안전 속성 ①: 마운트에 쓴 것이 클라우드에 도달하는가
    #
    # 이 질문을 전에는 보정기(ssot-push)가 살아 있는지로 재었다. 그 보정기는
    # **퇴역했다** — 재업로드가 DriveFS 와 경합해 Drive 에 동명 사본을 만들었다
    # (2026-09-07, 사유는 하트비트에 남아 있다). 퇴역한 것을 계속 ok 로 요구하면
    # 게이트에 영원히 꺼지지 않는 빨간불이 생기고, 그러면 사람이 게이트를 안 본다.
    # 그래서 **항목은 남기되** 기록된 결정 위에서 재고, 실제 안전 속성은 바로 아래
    # 수렴(해시 양방향) 검사가 짊어진다.
    d = hb.read("ssot-push")
    age = hb.age_min(d) if d else None
    retired = bool(d) and d.get("status") == "blocked" and "퇴역" in str(d.get("reason", ""))
    fresh = bool(d) and d.get("status") == "ok" and age is not None and age <= 90
    chk("W3", "마운트→클라우드 배포 경로", fresh or retired,
        (f"ssot-push 퇴역 — {str(d.get('reason'))[:60]} · 수렴 검사가 대신 잰다"
         if retired else
         f"ssot-push status={d.get('status') if d else '없음'}"
         + (f" · {age:.0f}분 전 (허용 90분)" if age is not None else " · 시각 불명")))
    # 유령 파일 (삭제 미전파의 결과) — 있으면 드러낸다
    gp = REPO / "state/ssot_ghosts.json"
    ng = 0
    if gp.exists():
        try:
            ng = sum(len(v) for v in json.loads(gp.read_text()).values())
        except ValueError:
            ng = -1
    chk("W3", "SSOT 유령 파일(클라우드 단독)", ng == 0,
        f"{ng}건 — ssot_push 가 탐지만 하고 지우지 않는다(마운트 지연과 실제 삭제를 구별 못 하므로)")
    # 진짜 안전 속성 ②: 수렴 — 해시로 양방향 확인
    conv = []
    for rel in ("50_도구상자", "20_전략"):
        src = MOUNT / rel
        if not src.is_dir():
            conv.append(f"{rel}:마운트없음"); continue
        for a, b, lab in ((str(src), f"SSOT_ro:{rel}", "미배포"),
                          (f"SSOT_ro:{rel}", str(src), "미반영")):
            try:
                q = subprocess.run(["rclone", "check", a, b, "--one-way", "--checksum",
                                    "--exclude", ".*"], capture_output=True, text=True,
                                   timeout=300)
            except subprocess.TimeoutExpired:
                # 마운트가 대량 변경을 따라잡는 중이면 해시 대조가 5분을 넘는다.
                # 그때 예외로 죽으면 **게이트 전체가 결과를 못 낸다** — 다른 검사까지 못 본다.
                # 느린 것은 "어긋났다"가 아니다. 판정 불가로 남기고 계속한다.
                conv.append(f"{rel}:{lab}(판정불가·타임아웃)")
                continue
            if q.returncode != 0:
                conv.append(f"{rel}:{lab}")
    chk("W3", "마운트↔클라우드 수렴(해시 양방향)", not conv,
        "; ".join(conv) or "감시 폴더 전부 0 diff")

    # health 보드 신선도
    hp = REPO / "state/health.md"
    age = (time.time() - hp.stat().st_mtime) / 60 if hp.exists() else 1e9
    chk("W3", "health 보드 15분 내 갱신", age < 20, f"{age:.0f}분 전")
    # Hermes 메타 감시
    try:
        env = {**os.environ, "HERMES_HOME": str(Path.home() / ".hermes/profiles/be_{{ORG_NAME}}")}
        j = subprocess.run(["hermes", "cron", "list"], env=env, capture_output=True,
                           text=True, timeout=60).stdout
    except Exception:                                        # noqa: BLE001
        j = ""
    chk("W3", "Hermes 와치독 메타 감시 등록", "{{WORKSPACE_SLUG}}-watchdog-meta" in j, "hermes cron list")
    # 경보 receipt
    rec = sorted((REPO / "state/alerts").glob("2026*.json")) if (REPO / "state/alerts").is_dir() else []
    ok = False
    if rec:
        try:
            ok = bool(json.loads(rec[-1].read_text()).get("receipt", {}).get("sent"))
        except ValueError:
            ok = False
    chk("W3", "경보 발송 receipt 존재", ok, f"{len(rec)}건 · 최신 {rec[-1].name if rec else '없음'}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--phase", default="all", choices=["W1", "W2", "W3", "all"])
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()
    # 단계 하나가 예외로 죽어도 나머지는 판정한다 — 게이트는 "무엇이 안 되는가"를
    # 보여주는 도구다. 한 검사의 타임아웃으로 전체가 침묵하면 그 역할을 못 한다.
    for name, fn in (("W1", w1), ("W2", w2), ("W3", w3)):
        if a.phase not in (name, "all"):
            continue
        try:
            fn()
        except Exception as e:                          # noqa: BLE001
            chk(name, f"{name} 단계 실행", None, f"{type(e).__name__}: {str(e)[:160]}")
    if a.json:
        print(json.dumps(R, ensure_ascii=False, indent=1))
    else:
        for x in R:
            mark = {"PASS": "✓", "FAIL": "✗", "BLOCKED": "◑"}[x["result"]]
            print(f"{mark} [{x['phase']}] {x['check']}")
            print(f"      {x['detail']}")
    f = [x for x in R if x["result"] == "FAIL"]
    b = [x for x in R if x["result"] == "BLOCKED"]
    print(f"\n판정: 통과 {len(R)-len(f)-len(b)} · 실패 {len(f)} · 차단 {len(b)} / 총 {len(R)}")
    # 종료코드 계약 (교차검증 C 지적 — BLOCKED 인데 exit 0 이던 모순)
    #   0 = 전항 통과   1 = 실패 있음   2 = 실패 없으나 차단 있음(미완)
    if f:
        print("  exit 1 — 실패 항목 있음")
        return 1
    if b:
        print(f"  exit 2 — 차단 {len(b)}건 (외부 조치 대기, 완료 아님)")
        return 2
    print("  exit 0 — 전항 통과")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
