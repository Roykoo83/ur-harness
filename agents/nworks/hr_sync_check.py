#!/usr/bin/env python3
"""Reconcile the Naver Works directory against the people ontology.

Read-only: reports drift, never edits the ontology or the directory.
Sources
  - Naver Works directory : nwctl users
  - Ontology              : 00_공통/50_온톨로지/사람/*.md frontmatter
  - HR events             : 경영관리부 주간업무보고 (입사/퇴사 lines)
Writes one dated report under 00_공통/50_온톨로지/_점검/.
"""
from pathlib import Path
import os
import re
import subprocess
import sys
import unicodedata
from datetime import datetime

# SSOT 는 **API 로** 본다. 마운트는 이 기계에만 있고, 아직 내려받지 않은 파일은
# 조용히 빈 본문이 된다 — 그러면 「온톨로지에 없다」가 거짓으로 나온다.
PEOPLE_REL = "00_공통/50_온톨로지/사람"
ARCHIVE_REL = "90_보관/컨플루언스_아카이브"
OUT_REL = "00_공통/50_온톨로지/_점검"


def _ssot_io():
    import sys as _s
    from pathlib import Path as _P
    _s.path.insert(0, str(_P(__file__).resolve().parents[2] / "core"))
    from lib import ssot_io                                    # noqa: PLC0415
    return ssot_io
NWCTL = str(Path(__file__).resolve().parent / "nwctl")


def nfc(s):
    return unicodedata.normalize("NFC", s)


def directory_names():
    try:
        out = subprocess.run([NWCTL, "users"], capture_output=True, text=True, timeout=120).stdout
    except Exception as e:
        return None, f"nwctl 실행 실패: {e}"
    names = set()
    for line in out.splitlines():
        m = re.search(r"'lastName':\s*'([^']*)'", line)
        if m and m.group(1).strip():
            names.add(nfc(m.group(1).strip()))
    return names, None


def ontology_people():
    """name -> {status, exitDate, joinDate}"""
    people = {}
    base = _ssot_io().stage(PEOPLE_REL)
    if base is None:
        raise SystemExit(f"온톨로지 `사람/`을 API 로 받지 못했다 ({PEOPLE_REL}) — "
                         "빈 결과로 넘어가면 전원이 「온톨로지없음」으로 보고된다")
    PEOPLE = str(base)
    for fn in os.listdir(PEOPLE):
        if not fn.endswith(".md") or fn.startswith("_"):
            continue
        name = nfc(fn[:-3])
        text = open(os.path.join(PEOPLE, fn), encoding="utf-8").read()
        fm = text.split("---", 2)[1] if text.startswith("---") else ""
        get = lambda k: (re.search(rf"^{k}:\s*\"?([^\"\n]+)\"?", fm, re.M) or [None, ""])[1].strip()
        people[name] = {
            "status": get("employmentStatus") or "(미기재)",
            "exit": get("exitDate"),
            "join": get("joinDate"),
            "internal": get("role") == "internal",
        }
    return people


def hr_events():
    """Scan 경영관리부 주간업무보고 for 입사/퇴사 lines."""
    events = []
    pat = re.compile(r"([가-힣]{2,4})\s*(?:리더|책임|매니저|CIO|팀장)?\s*(입사|퇴사)\s*\((\d{2}\.\d{2}\.\d{2})\)")
    # 대상은 **이름으로 먼저 걸러진다**(경영관리부 주간업무보고). 아카이브 전체를
    # 받을 필요가 없다 — 예전에 「4,147건이라 API 가 위험하다」고 적어 둔 것은
    # 이름 필터를 못 본 판단이었다 (2026-10-22 정정)
    arch = _ssot_io().stage(ARCHIVE_REL, include="*경영관리부*.md")
    if arch is None:
        raise SystemExit(f"컨플루언스 아카이브를 API 로 받지 못했다 ({ARCHIVE_REL}) — "
                         "빈 결과는 「인사 이벤트 없음」으로 읽혀 오판을 만든다")
    for root, _, files in os.walk(arch):
        for f in files:
            if not f.endswith(".md") or "경영관리부" not in nfc(f):
                continue
            try:
                text = open(os.path.join(root, f), encoding="utf-8", errors="ignore").read()
            except Exception:
                continue
            for name, kind, date in pat.findall(text):
                events.append((nfc(name), kind, "20" + date.replace(".", "-")))
    return sorted(set(events), key=lambda e: e[2], reverse=True)


def main():
    dir_names, err = directory_names()
    onto = ontology_people()
    evts = hr_events()

    lines = [f"# 조직도 ↔ 온톨로지 정합성 점검 ({datetime.now():%Y-%m-%d})", ""]

    if err:
        lines += [f"> ⚠️ {err}", ""]
        dir_names = set()
    else:
        lines += [f"네이버웍스 조직도 {len(dir_names)}명 · 온톨로지 사람 {len(onto)}명", ""]

    # 1) HR events not reflected in the ontology
    lines += ["## 1. 인사 이벤트 미반영", ""]
    drift = []
    for name, kind, date in evts:
        p = onto.get(name)
        if p is None:
            drift.append(f"- **{name}** {kind}({date}) — 온톨로지 엔트리 **없음**")
        elif kind == "퇴사" and p["status"] != "resigned":
            drift.append(f"- **{name}** 퇴사({date}) — 온톨로지 상태 `{p['status']}` (resigned 필요)")
        elif kind == "입사" and not p["join"]:
            drift.append(f"- {name} 입사({date}) — joinDate 미기재")
    lines += (drift or ["- 없음"]) + [""]

    # 2) resigned people still listed in the directory
    lines += ["## 2. 퇴사자인데 조직도에 잔존", ""]
    stale = [f"- **{n}** (퇴사 {p['exit'] or '일자미상'}) — 조직도 계정 정리 필요"
             for n, p in onto.items() if p["status"] == "resigned" and n in dir_names]
    lines += (stale or ["- 없음"]) + [""]

    # 3) in the directory but missing from the ontology (people only)
    lines += ["## 3. 조직도에 있으나 온톨로지 없음", ""]
    def is_person(n):
        if n.startswith("(주)") or re.fullmatch(r"[A-Za-z0-9_ ()]+", n):
            return False  # system / shared / vendor accounts
        if any(k in n for k in ("관리자", "관리", "문의", "클라우드", "계정")):
            return False
        return 2 <= len(n) <= 4 and re.fullmatch(r"[가-힣]+", n) is not None
    missing = [f"- **{n}**" for n in sorted(dir_names - set(onto)) if is_person(n)]
    lines += (missing or ["- 없음"]) + [""]

    # 4) active internal staff absent from the directory
    lines += ["## 4. 재직자인데 조직도 미등재", ""]
    absent = [f"- **{n}** (입사 {p['join'] or '일자미상'}) — 계정 생성/동기화 필요"
              for n, p in onto.items()
              if p["internal"] and p["status"] == "active" and n not in dir_names]
    lines += (absent or ["- 없음"]) + [""]

    lines += ["---", "",
              "생성: `scripts/agents/hr_sync_check.py` (읽기 전용 — 온톨로지·조직도를 수정하지 않음)",
              "인사 이벤트 출처: 경영관리부 주간업무보고"]

    io = _ssot_io()
    name = f"조직도_온톨로지_점검_{datetime.now():%Y%m%d}.md"
    from lib.config import paths as _p                          # noqa: PLC0415
    tmp = _p.state / "_hr_sync" / name
    tmp.parent.mkdir(parents=True, exist_ok=True)
    tmp.write_text("\n".join(lines) + "\n", encoding="utf-8")
    ok, msg = io.put(tmp, f"{OUT_REL}/{name}")
    if not ok:
        # 올리지 못했으면 **성공으로 끝내지 않는다.** 보고서가 없는데 0 을
        # 돌려주면 다음 사람이 「점검했는데 문제 없었다」로 읽는다
        print(f"보고서 업로드 실패: {msg}")
        print(f"로컬 사본: {tmp}")
        return 1
    print(f"{OUT_REL}/{name}")
    print(f"미반영 {len(drift)} · 잔존 {len(stale)} · 온톨로지없음 {len(missing)} · 조직도없음 {len(absent)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
