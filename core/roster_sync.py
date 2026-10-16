#!/usr/bin/env python3
"""인원 명부 동기화 — 네이버웍스 조직도를 정본으로 온톨로지 `사람/`을 맞춘다.

Roy 지시(2026-09-22): "내가 주는 건 의미가 없다. 이런 데이터는 우리가 사용하는 시스템을
기반으로 계속 성장해야 한다."

왜 네이버웍스인가
  회사가 실제로 쓰는 계정 시스템이다. 입·퇴사가 여기 반영되고, 부서·직급이 여기 있다.
  직무기술서(15명)는 작성한 사람만 있고, 온톨로지(23명)는 메일·제안서에서 *관측된* 사람만
  있었다. 둘의 교집합이 4명뿐이었다 (2026-09-22 실측). 어느 쪽도 명부가 아니다.

개인정보 경계
  전화번호·생년월일·개인메일은 **가져오지 않는다.** 이름·회사메일·부서·직급·직책만 쓴다.
  (SSOT README 반입 금지 규범)

쓰기 규칙
  · `00_공통/50_온톨로지/사람/` 은 사람 구역 → **마운트로** 쓴다 (구역표)
  · frontmatter 의 명부 필드만 갱신한다. **본문과 다른 필드는 건드리지 않는다**
  · 사람 파일을 **지우지 않는다.** 디렉토리에 없으면 status 만 바꾸고 사유를 남긴다
  · 디렉토리에만 있는 사람은 새로 만든다 (그것이 "자란다"의 뜻)

사용: roster_sync.py [--apply]   (기본 dry-run)
"""
from __future__ import annotations
import argparse, glob, os, re, sys, unicodedata
from datetime import date
from pathlib import Path

sys.path.insert(0, "{{HOME}}/{{WORKSPACE_NAME}}/projects/nworks-agent/scripts")

SSOT = Path("{{MOUNT_ROOT}}/"
            "공유 드라이브/{{SSOT_DRIVE}}")
PEOPLE = SSOT / "00_공통/50_온톨로지/사람"
ORG = SSOT / "00_공통/10_조직"
TODAY = date.today().isoformat()

# 사람이 아닌 계정이 모여 있는 부서 — 봇·클라우드·프로젝트 계정
NON_PERSON_DEPT = {"기타계정", "업무용 부서"}
# 부서가 비어 있는 계정 중 사람이 아닌 것 (이름으로 판별 불가해 명시한다)
NON_PERSON_NAME = {"자폐스펙트럼과제", "해커톤", "텐센트클라우드", "{{BOT_USER}}"}
HANGUL_NAME = re.compile(r"^[가-힣]{2,4}$")

FIELDS = ("status", "department", "position", "jobLevel", "email", "role",
          "rosterSource", "rosterObserved", "rosterNote")


def nfc(s: str) -> str:
    return unicodedata.normalize("NFC", s)


def S(v) -> str:
    return "" if v is None or v == "None" else str(v)


# ── 원천 1: 네이버웍스 조직도 ───────────────────────────────────────────
def from_naverworks() -> dict[str, dict]:
    from nworks.client import NworksClient                      # noqa: PLC0415
    from nworks.domains import directory_users                  # noqa: PLC0415
    out = {}
    for u in directory_users(NworksClient()):
        nm = u.get("userName") or {}
        name = nfc((S(nm.get("lastName")) + S(nm.get("firstName"))).strip())
        orgs = u.get("organizations") or [{}]
        o = orgs[0] or {}
        ou = [x for x in (o.get("orgUnits") or []) if x] or [{}]
        dept = " / ".join(S(x.get("orgUnitName")) for x in ou if S(x.get("orgUnitName")))
        if dept in NON_PERSON_DEPT or name in NON_PERSON_NAME or not HANGUL_NAME.match(name):
            continue
        out[name] = {
            "status": "재직",
            "department": dept,
            "jobLevel": S(o.get("levelName")),          # 리더·책임·매니저·이사·대표이사
            "position": S(ou[0].get("positionName")),   # 팀장·COO·CIO
            "email": S(u.get("email")),
            "rosterSource": "naverworks-directory",
            "rosterObserved": TODAY,
        }
    return out


# ── 원천 2: 직무기술서 파일명 (과거 인원 포함) ──────────────────────────
def from_job_descriptions() -> set[str]:
    out = set()
    for p in glob.glob(str(ORG / "*직무기술서*")):
        m = re.search(r"_([가-힣]{2,4})\.(hwp|hwpx|pdf|docx)$", nfc(os.path.basename(p)))
        if m:
            out.add(m.group(1))
    return out


# ── 원천 3: 기존 온톨로지 ──────────────────────────────────────────────
def from_ontology(internal_only: bool = True) -> dict[str, tuple[Path, str]]:
    """사람 페이지를 이름(괄호 별칭 제거)으로 묶어 돌려준다.

    internal_only=False 는 `role: external` 로 잘못 적힌 사람까지 본다. 디렉토리에 회사
    계정이 있으면 그 사람은 내부다 — 온톨로지는 메일에서 *관측*한 것이라 외부로 오판했다
    (김원주·박지훈·오성하, 2026-09-22 실측).
    """
    out = {}
    for p in sorted(PEOPLE.glob("*.md")):
        b = nfc(p.name)
        if b.startswith("_"):
            continue
        try:
            head = p.read_text(encoding="utf-8")[:1800]
        except OSError:
            continue
        if internal_only and ("{{ORG_NAME}}" not in head
                              or not re.search(r'role:\s*"?internal', head)):
            continue
        core = re.sub(r"\s*\(.*?\)\s*", "", b[:-3]).strip()
        out.setdefault(core, []).append((p, head))
    return out


def set_field(text: str, key: str, val: str) -> str:
    """frontmatter 의 key 를 갱신·삽입한다. 없으면 `title:` 바로 뒤에 넣는다."""
    if not text.startswith("---"):
        text = f"---\ntype: wiki-page\n---\n\n# {key}\n" + text
    end = text.index("\n---", 3)
    head, body = text[:end], text[end:]
    line = f'{key}: "{val}"'
    pat = re.compile(rf'^{re.escape(key)}:.*$', re.M)
    if pat.search(head):
        head = pat.sub(line, head, count=1)
    else:
        m = re.search(r'^title:.*$', head, re.M)
        head = (head[:m.end()] + "\n" + line + head[m.end():]) if m else head + "\n" + line
    return head + body


NEW_TMPL = """---
type: wiki-page
subtype: note
layer: entities
class: Person
entityClass: Person
businessKey: ""
title: "{name}"
status: "재직"
department: "{dept}"
jobLevel: "{level}"
position: "{pos}"
email: "{email}"
rosterSource: "naverworks-directory"
rosterObserved: "{today}"
aliases: []
description: "{{ORG_NAME}} {dept} {level}"
role: "internal"
tags:
  - ontology
  - entity
  - Person
employedBy:
  - "[[{{ORG_NAME}}]]"
provenance: "directory"
source: "naverworks-directory"
security: "S1"
date modified: {today}T00:00:00+09:00
---
# {name}

네이버웍스 조직도에서 자동 생성 ({today}). 아직 관측된 활동이 없다 —
메일·제안서·회의록에서 이 사람이 나타나면 여기에 쌓인다.
"""


ROSTER_DOC = ORG / "_명부.md"


def write_roster_doc(nw: dict[str, dict], onto: dict, jd: set[str]) -> None:
    """사람이 읽는 명부 한 장. 손으로 쓰지 않는다 — 매번 디렉토리에서 다시 만든다.

    이 파일이 "자란다"의 눈에 보이는 형태다. 입·퇴사가 네이버웍스에 반영되면 다음 실행에서
    여기 반영된다. 전화번호·생년월일은 넣지 않는다.
    """
    by_dept: dict[str, list] = {}
    for n, r in nw.items():
        by_dept.setdefault(r["department"] or "(미지정)", []).append((n, r))
    ORDER = {"대표이사": 0, "이사": 1, "리더": 2, "책임": 3, "매니저": 4, "": 9}
    L = [f"# 인원 명부", "",
         f"> 자동 생성 {TODAY} · 원천 **네이버웍스 조직도** · `scripts/roster_sync.py`",
         "> 손으로 고치지 마세요 — 다음 실행에 덮어씁니다. 틀렸으면 네이버웍스를 고치면 여기가 따라옵니다.",
         "> 전화번호·생년월일·개인메일은 담지 않습니다(SSOT 반입 금지 규범).", "",
         f"**재직 {len(nw)}명 · 부서 {len(by_dept)}개**", ""]
    for d in sorted(by_dept, key=lambda x: (x == "(미지정)", x)):
        people = sorted(by_dept[d], key=lambda kv: (ORDER.get(kv[1]["jobLevel"], 5), kv[0]))
        L += [f"## {d}  ({len(people)})", "", "| 이름 | 직급 | 직책 | 회사메일 | 온톨로지 |", "|---|---|---|---|---|"]
        for n, r in people:
            has = "○" if n in onto else "—"
            L.append(f"| {n} | {r['jobLevel'] or '-'} | {r['position'] or '-'} | `{r['email']}` | {has} |")
        L.append("")
    gone = sorted(set(jd) - set(nw))
    if gone:
        L += ["## 계정 없음 — 과거 재직 추정", "",
              "직무기술서는 있으나 네이버웍스 계정이 없는 사람. 퇴사 여부는 경영관리부 주간업무보고로 확인해야 합니다.", "",
              "  " + " · ".join(gone), ""]
    L += ["## 이 명부가 없던 동안", "",
          "2026-09-22 실측: 직무기술서 15명과 온톨로지 23명의 **교집합이 4명**이었습니다.",
          "어느 쪽도 명부가 아니었고, 그래서 AI 가 회사 인원을 틀리게 알고 있었습니다.",
          "지금은 네이버웍스가 정본이고, 온톨로지·직무기술서는 그것과 대조되는 관측입니다.", ""]
    ROSTER_DOC.write_text("\n".join(L), encoding="utf-8")
    print(f"✓ 명부 문서 — {ROSTER_DOC.relative_to(SSOT)}  ({len(nw)}명 · {len(by_dept)}부서)")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    a = ap.parse_args()

    nw = from_naverworks()
    jd = from_job_descriptions()
    onto = from_ontology()                       # 내부로 적힌 사람
    every = from_ontology(internal_only=False)   # 파일이 있는 모든 사람
    # 디렉토리에 있는데 온톨로지가 external 로 적어 둔 사람 → 내부로 교정 (신규 생성 아님)
    misread = {n: every[n] for n in nw if n not in onto and n in every}
    onto.update(misread)

    plan_update, plan_new, plan_status, dup, conflict = [], [], [], [], []

    for name, rec in sorted(nw.items()):
        if name in onto:
            files = onto[name]
            if len(files) > 1:
                dup.append((name, [f.name for f, _ in files]))
            p, head = files[0]
            cur = {k: (re.search(rf'^{k}:\s*"?([^"\n]*)', head, re.M) or [None, ""])[1]
                   for k in FIELDS}
            rec = dict(rec)
            # 계정이 살아 있다고 재직의 증거는 아니다. 퇴사 기록은 주간업무보고(경영관리부)에서
            # 온 것이고 계정은 퇴사 후에도 한동안 남는다. 덮어쓰지 않고 충돌로 남긴다.
            # **본인의** 퇴사만 센다. 후임·인수인계 문맥에서 남의 퇴사가 언급된다
            # (황동식.md 가 전임 [[{{MEMBER_D}}]] 리더 퇴사를 적고 있었다 — 2026-09-22 실측).
            # 판정: description 필드에 퇴사가 있거나, 위키링크 없는 줄의 "날짜 + 퇴사".
            when = ""
            desc = (re.search(r'^description:\s*"?([^"\n]*)', head, re.M) or [None, ""])[1]
            if "퇴사" in desc:
                when = (re.search(r"(\d{4}-\d{2}-\d{2})", desc) or [None, ""])[1]
                leave = True
            else:
                leave = False
                for ln in head.splitlines():
                    if "퇴사" in ln and "[[" not in ln:
                        m = re.search(r"(\d{4}-\d{2}-\d{2})\s*퇴사|퇴사[^\n]{0,12}?(\d{4}-\d{2}-\d{2})", ln)
                        if m:
                            when = m.group(1) or m.group(2); leave = True; break
            if leave:
                rec["status"] = "확인필요"
                rec["rosterNote"] = (f"온톨로지에 퇴사 기록({when or '날짜없음'}) · "
                                     f"네이버웍스 계정은 존재 — 사람이 판단")
                conflict.append((name, when))
            if name in misread:
                rec["role"] = "internal"
                rec["rosterNote"] = "네이버웍스 계정 보유 — external 로 잘못 적혀 있던 것을 교정"
            diff = {k: v for k, v in rec.items()
                    if v and (k == "role" or cur.get(k, "") != v)}
            if diff:
                plan_update.append((p, name, diff))
        else:
            plan_new.append((name, rec))

    for name, files in sorted(onto.items()):
        if name in nw:
            continue
        p, head = files[0]
        cur = (re.search(r'^status:\s*"?([^"\n]*)', head, re.M) or [None, ""])[1]
        why = "직무기술서 있음 — 과거 재직" if name in jd else "디렉토리·직무기술서 모두 없음"
        if cur != "미확인(계정없음)":
            plan_status.append((p, name, why))

    # ── 출력 ─────────────────────────────────────────────────────────
    print(f"네이버웍스 사람 {len(nw)} · 온톨로지 내부 {len(onto)} · 직무기술서 {len(jd)}")
    print(f"\n■ 신규 생성 {len(plan_new)}")
    for n, r in plan_new:
        print(f"   + {n:<8} {r['department']:<16} {r['jobLevel']:<8} {r['position']}")
    print(f"\n■ 필드 갱신 {len(plan_update)}")
    for p, n, d in plan_update:
        print(f"   ~ {n:<8} " + " · ".join(f"{k}={v}" for k, v in d.items())[:100])
    print(f"\n■ 계정 없음 → status 표시 {len(plan_status)}  (파일은 지우지 않는다)")
    for p, n, w in plan_status:
        print(f"   ? {n:<8} {w}")
    if conflict:
        print(f"\n■ ★충돌 {len(conflict)} — 온톨로지 퇴사 기록 vs 계정 존재 (status=확인필요)")
        for n, w in conflict:
            print(f"   ! {n:<8} 퇴사 기록 {w or '(날짜없음)'} · 네이버웍스 계정 있음")
    if dup:
        print(f"\n■ 중복 파일 {len(dup)}  (사람이 병합 판단)")
        for n, fs in dup:
            print(f"   ! {n}: {' | '.join(fs)}")

    if not a.apply:
        print("\n[dry-run] --apply 로 실행")
        return 0

    n_ok = 0
    for p, name, diff in plan_update:
        t = p.read_text(encoding="utf-8")
        for k, v in diff.items():
            t = set_field(t, k, v)
        p.write_text(t, encoding="utf-8"); n_ok += 1
        print(f"✓ 갱신 {name}")
    for name, rec in plan_new:
        p = PEOPLE / f"{name}.md"
        if p.exists():
            print(f"! 건너뜀(이미 있음) {name}"); continue
        p.write_text(NEW_TMPL.format(name=name, dept=rec["department"], level=rec["jobLevel"],
                                     pos=rec["position"], email=rec["email"], today=TODAY),
                     encoding="utf-8"); n_ok += 1
        print(f"✓ 생성 {name}")
    for p, name, why in plan_status:
        t = p.read_text(encoding="utf-8")
        t = set_field(t, "status", "미확인(계정없음)")
        t = set_field(t, "rosterObserved", TODAY)
        t = set_field(t, "rosterNote", why)
        p.write_text(t, encoding="utf-8"); n_ok += 1
        print(f"✓ status {name} — {why}")
    write_roster_doc(nw, onto, jd)
    print(f"\n완료 {n_ok}건")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
