#!/usr/bin/env python3
"""기존 서비스 하트비트 계측 (비침습) — 보완기획 §6-2

7개 가동 서비스를 수정하지 않고, **각 서비스가 남긴 증거**에서 하트비트를 유도한다.
로그 파일의 mtime 이 아니라 **로그 본문의 마지막 성공 타임스탬프**를 파싱한다 —
mtime 은 rclone·편집 등으로 갱신될 수 있어 신선도 신호가 아니다(실측 #16).

서비스 코드를 API 경로로 옮길 때(§3-2 e·f) 각자 hb.ok() 를 직접 부르도록 바꾼다.
그때까지 이 어댑터가 감시 공백을 메운다.
"""
from __future__ import annotations
import json, re, sys, time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from lib import hb

H = Path.home()
NW = H / "{{WORKSPACE_NAME}}/projects/nworks-agent"
RP = H / "{{WORKSPACE_NAME}}/projects/rpbd-infra"
FA = H / "{{WORKSPACE_NAME}}/projects/fathom-agent"
WD = H / "{{WORKSPACE_NAME}}/projects/urban-weekly-dashboard"
# SSOT 는 **API 로 읽는다**(SSOT_ro). 마운트는 8일치 낡은 상태를 최신인 척 보여준 전력이 있고
# (2026-09-04 KTL 사고), 이 어댑터는 15분마다 도는 최다 빈도 소비자다.
import subprocess as _sp


def ssot_read(rel: str) -> str | None:
    """SSOT 파일 본문을 API 로 읽는다."""
    try:
        r = _sp.run(["rclone", "cat", f"SSOT_ro:{rel}"], capture_output=True, text=True, timeout=120)
        return r.stdout if r.returncode == 0 else None
    except Exception:                                        # noqa: BLE001
        return None


def ssot_list(rel: str) -> list[str]:
    """SSOT 폴더의 파일명 목록을 API 로 읽는다."""
    try:
        r = _sp.run(["rclone", "lsf", "--files-only", f"SSOT_ro:{rel}"],
                    capture_output=True, text=True, timeout=120)
        return sorted(x for x in r.stdout.splitlines() if x.strip()) if r.returncode == 0 else []
    except Exception:                                        # noqa: BLE001
        return []

TS = re.compile(r"\[(\d{4}-\d{2}-\d{2})[ T](\d{2}:\d{2}:\d{2})\]")


def last_ts(path: Path, needle: str, tail: int = 4000) -> str | None:
    """로그 뒤쪽에서 needle 을 포함한 마지막 줄의 타임스탬프."""
    try:
        lines = path.read_text(encoding="utf-8", errors="ignore").splitlines()[-tail:]
    except OSError:
        return None
    for ln in reversed(lines):
        if needle in ln:
            m = TS.search(ln)
            if m:
                return f"{m.group(1)}T{m.group(2)}+0900"
    return None


def put(name: str, iso: str | None, cursor=None, counters=None, note="") -> str:
    if not iso:
        hb.no_evidence(name, "로그에서 성공 타임스탬프를 찾지 못함")
        return f"  ◑ {name}: 증거 없음(계측 실패 — 서비스 장애와 구별됨)"
    p = hb.HB_DIR / f"{name}.json"
    payload = {"schema_version": hb.SCHEMA_VERSION, "service": name, "status": "ok",
               "run_id": f"adopt-{time.strftime('%Y%m%dT%H%M%S')}", "host": "adopted",
               "completed_at": iso, "expected_max_age_min": hb.EXPECTED_MAX_AGE_MIN.get(name, 120),
               "input_cursor": cursor or {}, "outputs": [], "counters": counters or {},
               "note": note or "hb_adopt.py 유도 (서비스 미수정)"}
    hb.HB_DIR.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
    age = hb.age_min(payload)
    return f"  ✓ {name}: {iso[:16]} ({age:.0f}분 전)"


def main() -> int:
    out = []
    # nworks-mail — 사이클 로그 + 수집 cursor(total_seen)
    cur = {}
    try:
        st = json.loads((NW / "output/raw_mail/{{BOT_USER}}/state.json").read_text())
        cur = {"kind": "mailId", "value": max((int(k) for k in st.get("seen", {})), default=0)}
    except (OSError, ValueError):
        pass
    out.append(put("nworks-mail", last_ts(NW / "workspace/mail_cycle.log", "sync done"), cur))

    # nworks-brief
    out.append(put("nworks-brief", last_ts(NW / "workspace/daily_brief.log", "브리핑 발송 완료")))

    # rpbd-tasks
    out.append(put("rpbd-tasks", last_ts(RP / "workspace/tasks_hourly.log", "===== 완료 =====")))

    # fathom-minutes — 로그에 날짜가 없어 [HH:MM:SS] 만 있으므로 파일 mtime 날짜와 결합
    fl = FA / "workspace/fathom-minutes.log"
    iso = None
    try:
        lines = fl.read_text(encoding="utf-8", errors="ignore").splitlines()
        for ln in reversed(lines[-500:]):
            if "완료" in ln:
                m = re.search(r"\[(\d{2}:\d{2}:\d{2})\]", ln)
                if m:
                    d = time.strftime("%Y-%m-%d", time.localtime(fl.stat().st_mtime))
                    iso = f"{d}T{m.group(1)}+0900"
                    break
    except OSError:
        pass
    out.append(put("fathom-minutes", iso, note="로그 [HH:MM:SS] + 파일 날짜 결합"))

    # ssot-index — manifest 의 generated 필드가 서비스 자신의 기록
    iso = None
    try:
        _raw = ssot_read("_시스템/색인/manifest.json")
        mf = json.loads(_raw) if _raw else {}
        g = mf.get("generated") or (mf.get(".") or {}).get("generated")
        if not g:
            for v in mf.values():
                if isinstance(v, dict) and v.get("generated"):
                    g = v["generated"]; break
        iso = g
    except (OSError, ValueError):
        pass
    out.append(put("ssot-index", iso, note="manifest.json generated"))

    # hr-sync-check — 산출물(점검 md) 최신 날짜를 파일명에서
    iso = None
    try:
        chks = [x for x in ssot_list("00_공통/50_온톨로지/_점검") if x.startswith("조직도_온톨로지_점검_")]
        if chks:
            m = re.search(r"(\d{8})", sorted(chks)[-1])
            if m:
                iso = f"{m.group(1)[:4]}-{m.group(1)[4:6]}-{m.group(1)[6:]}T09:17:00+0900"
    except Exception:                                        # noqa: BLE001
        pass
    out.append(put("hr-sync-check", iso, note="점검 산출물 파일명 날짜"))

    # weekly-dashboard — 산출물 mtime (주 1회 배치라 mtime=생성시각)
    iso = None
    try:
        f = WD / "output/index.html"
        iso = time.strftime("%Y-%m-%dT%H:%M:%S+0900", time.localtime(f.stat().st_mtime))
    except OSError:
        pass
    out.append(put("weekly-dashboard", iso, note="output/index.html mtime"))

    print("\n".join(out))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
