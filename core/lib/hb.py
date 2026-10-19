#!/usr/bin/env python3
"""하트비트 레지스트리 — 데이터취득계층_보완기획_260904.md §6-2

와치독은 로그를 읽지 않는다. **산출물의 나이가 아니라 입력 cursor** 를 본다.
rclone 은 원천 수정시각을 보존하므로 mtime 은 관측시각이 아니다(실측 #16).

  hb.ok("ssot-promote", cursor={"kind":"mailId","value":370}, outputs=[...])
  hb.fail("ssot-promote", "SSOT 접근 불가")

실패도 기록한다 — "침묵"과 "실패"를 구별하기 위해서다.
"""
from __future__ import annotations
import json, os, socket, tempfile, time
from pathlib import Path

ROOT = Path(os.environ.get("UR_STATE", Path.home() / "{{WORKSPACE_NAME}}" / "state"))
HB_DIR = ROOT / "heartbeat"
SCHEMA_VERSION = "hb/1.0"

# 서비스별 허용 나이(분). 와치독이 이 값으로 지연을 판정한다.
EXPECTED_MAX_AGE_MIN = {
    "nworks-mail": 90,        # 1시간 주기
    "ssot-promote": 45,       # 30분 주기
    "ssot-push": 90,          # 1시간 주기 — 마운트→클라우드 배포 보정
    "u-changefeed": 180,      # 2시간 주기
    "ssot-index": 240,        # 3시간 주기
    "rpbd-tasks": 240,        # 3시간 주기
    "watchdog": 30,           # 15분 주기
    "nworks-brief": 1500,     # 평일 2회
    "weekly-dashboard": 11000,  # 주 1회
    "fathom-minutes": 11000,    # 주 1회
    "hr-sync-check": 11000,     # 주 1회
}


def _write(name: str, payload: dict) -> Path:
    HB_DIR.mkdir(parents=True, exist_ok=True)
    dst = HB_DIR / f"{name}.json"
    fd, tmp = tempfile.mkstemp(dir=str(HB_DIR), prefix=f".{name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=1)
            f.flush(); os.fsync(f.fileno())
        os.replace(tmp, dst)          # 원자 기록
    except BaseException:
        os.unlink(tmp); raise
    return dst


def _base(name: str, status: str) -> dict:
    return {
        "schema_version": SCHEMA_VERSION,
        "service": name,
        "status": status,
        "run_id": f"{time.strftime('%Y%m%dT%H%M%S')}-{os.getpid()}",
        "host": socket.gethostname(),
        "completed_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "expected_max_age_min": EXPECTED_MAX_AGE_MIN.get(name, 120),
    }


def ok(name: str, cursor: dict | None = None, outputs: list | None = None,
       counters: dict | None = None, note: str = "") -> Path:
    """마지막 성공 지점에서만 호출한다."""
    p = _base(name, "ok")
    p["input_cursor"] = cursor or {}
    p["outputs"] = outputs or []
    p["counters"] = counters or {}
    if note:
        p["note"] = note
    return _write(name, p)


def partial(name: str, targets: dict, cursor: dict | None = None, **kw) -> Path:
    """목적지 일부만 성공. 정상으로 오인되지 않도록 별도 상태로 남긴다."""
    p = _base(name, "partial")
    p["targets"] = targets
    p["input_cursor"] = cursor or {}
    p.update({k: v for k, v in kw.items() if k in ("outputs", "counters", "note")})
    return _write(name, p)


def no_evidence(name: str, reason: str) -> Path:
    """계측 실패(로그 파싱 불가) — **서비스 실패와 다르다**.
    서비스는 정상인데 우리 어댑터가 증거를 못 읽은 경우다 (교차검증 B③).
    """
    p = _base(name, "no_evidence")
    p["reason"] = str(reason)[:500]
    p["needs"] = "hb_adopt.py 파싱 규칙 점검 (서비스 자체 장애가 아닐 수 있음)"
    return _write(name, p)


def blocked(name: str, reason: str, needs: str = "") -> Path:
    """알려진 외부 차단(권한·승인 대기). '조용한 고장'과 구별해 경보를 내지 않는다."""
    p = _base(name, "blocked")
    p["reason"] = str(reason)[:500]
    if needs:
        p["needs"] = needs
    return _write(name, p)


def fail(name: str, reason: str, cursor: dict | None = None) -> Path:
    p = _base(name, "fail")
    p["reason"] = str(reason)[:500]
    p["input_cursor"] = cursor or {}
    return _write(name, p)


def read(name: str) -> dict | None:
    try:
        return json.loads((HB_DIR / f"{name}.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def age_min(hb: dict) -> float | None:
    """completed_at 의 나이(분). 파싱 불가면 None."""
    ts = hb.get("completed_at")
    if not ts:
        return None
    try:
        import datetime as dt
        t = dt.datetime.strptime(ts, "%Y-%m-%dT%H:%M:%S%z")
        return (dt.datetime.now(t.tzinfo) - t).total_seconds() / 60
    except ValueError:
        return None


def validate(hb: dict) -> list[str]:
    """스키마 위반 목록. 비어 있으면 통과. 와치독이 '존재'가 아니라 이것을 본다."""
    errs = []
    if hb.get("schema_version") != SCHEMA_VERSION:
        errs.append(f"schema_version != {SCHEMA_VERSION}")
    for k in ("service", "status", "run_id", "completed_at", "expected_max_age_min"):
        if not hb.get(k):
            errs.append(f"필수 필드 없음: {k}")
    if hb.get("status") not in ("ok", "partial", "fail", "blocked", "no_evidence"):
        errs.append(f"status 값 이상: {hb.get('status')}")
    a = age_min(hb)
    if a is None:
        errs.append("completed_at 파싱 불가")
    elif a < -5:
        errs.append(f"미래 시각 ({a:.0f}분)")
    return errs
