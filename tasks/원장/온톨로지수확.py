#!/usr/bin/env python3
"""메일 분류 결과에서 온톨로지 성장분을 거둔다 — **제안까지만 만든다.**

    python3 scripts/원장/온톨로지수확.py

왜 제안까지인가
    온톨로지(`00_공통/50_온톨로지/`)는 사람 구역이다. 기계가 바로 쓰면 「브이터치」와
    「브이터치(VTouch)」 같은 중복이 생긴다 — 실제로 있었던 일이다.
    그래서 **빈 키를 채우는 것과 새 엔티티를 만드는 것을 가른다.**

      · 기존 엔티티의 **빈** `businessKey`·`aliases` 를 채운다  → 되돌릴 수 있고 충돌이 없다
      · 새 엔티티를 만든다                                    → 제안 파일에만 쓴다

    어느 쪽도 이 스크립트가 SSOT 에 쓰지 않는다. 산출은 로컬 제안 파일 하나이고,
    사람이 보고 반영한다. 덮어쓰기와 삭제는 하지 않는다.

산출
    workspace/재정비/온톨로지/제안_YYYY-MM-DD.md    사람이 읽고 판단
    workspace/재정비/온톨로지/키채움_YYYY-MM-DD.json 기존 엔티티에 붙일 이메일·도메인
"""
from __future__ import annotations

import datetime as dt
import glob
import json
import os
import re
import unicodedata
from pathlib import Path

PROJ = Path(__file__).resolve().parents[2]
ONT = (Path.home() / "{{MOUNT_REL}}"
       / "공유 드라이브" / "{{SSOT_DRIVE}}" / "00_공통" / "50_온톨로지")
CLASSIFIED = PROJ / "workspace" / "재정비" / "메일분류"
OUT = PROJ / "workspace" / "재정비" / "온톨로지"

nfc = lambda s: unicodedata.normalize("NFC", s or "")     # noqa: E731
# 사람이 아니다. 기계가 보내는 주소는 엔티티로 만들지 않는다.
MACHINE = re.compile(r"(no-?reply|noreply|donotreply|notification|mailer-daemon|"
                     r"drive-shares|confluence@|team@mail\.|@email\.|automated)", re.I)
# 우리 회사. 사내 사람은 인사 원장이 정본이라 여기서 만들지 않는다.
INHOUSE = "{{ORG_DOMAIN}}"


def read_ontology() -> tuple[dict, dict]:
    """(이름→파일경로, 이름→frontmatter). 마운트가 잠기면 그 사실을 남기고 계속한다."""
    names, fm = {}, {}
    for cls in ("사람", "조직", "고객", "과제"):
        for p in sorted((ONT / cls).glob("*.md")) if (ONT / cls).is_dir() else []:
            try:
                text = p.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                continue
            n = nfc(p.stem)
            names[n] = p
            m = re.match(r"---\n(.*?)\n---", text, re.S)
            d = {"_class": cls, "_body": text}
            if m:
                for line in m.group(1).splitlines():
                    k = re.match(r"^([A-Za-z가-힣_]+):\s*(.*)$", line)
                    if k:
                        d[k.group(1)] = k.group(2).strip().strip('"')
            fm[n] = d
    return names, fm


def collect() -> tuple[dict, dict]:
    """분류 결과에서 (사람, 조직) 후보를 모은다. 등장 횟수와 근거를 함께 센다."""
    people, orgs = {}, {}
    for f in sorted(CLASSIFIED.glob("분류결과_*.jsonl")):
        for line in f.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                r = json.loads(line)
            except ValueError:
                continue
            for p in r.get("people") or []:
                em = (p.get("email") or "").lower().strip()
                nm = nfc(p.get("name"))
                if not em or MACHINE.search(em) or not nm:
                    continue
                rec = people.setdefault(em, {"name": nm, "org": p.get("org") or "",
                                             "n": 0, "proj": set()})
                rec["n"] += 1
                if r.get("proj"):
                    rec["proj"].add(r["proj"])
            for o in r.get("orgs") or []:
                dom = (o.get("domain") or "").lower().strip()
                nm = nfc(o.get("name"))
                if not dom or not nm or dom == INHOUSE:
                    continue
                rec = orgs.setdefault(dom, {"name": nm, "n": 0, "proj": set()})
                rec["n"] += 1
                if r.get("proj"):
                    rec["proj"].add(r["proj"])
    return people, orgs


def match_name(nm: str, names: dict) -> str | None:
    """온톨로지에 이미 있는가. 표기 차이(JASON AN ↔ {{MEMBER_C}})는 여기서 못 잡는다 —
    못 잡으면 신규 제안으로 가고, 사람이 합칠지 정한다."""
    n = nfc(nm).replace(" ", "")
    for k in names:
        if k.replace(" ", "") == n:
            return k
    for k in names:                       # 부분 일치는 길이 2 이상일 때만
        kk = k.replace(" ", "")
        if len(n) >= 2 and (n in kk or kk in n):
            return k
    return None


def main() -> int:
    if not ONT.is_dir():
        print("!! 온톨로지 경로를 읽을 수 없습니다 (마운트 확인)")
        return 1
    names, fm = read_ontology()
    people, orgs = collect()
    today = dt.date.today().isoformat()

    fill, new_people, new_orgs = [], [], []
    for em, r in sorted(people.items(), key=lambda x: -x[1]["n"]):
        hit = match_name(r["name"], names)
        if hit:
            cur = fm.get(hit, {})
            has_key = (cur.get("businessKey") or "").strip(' "')
            if has_key:
                continue                  # **이미 있는 값은 건드리지 않는다**
            fill.append({"class": cur.get("_class"), "name": hit, "필드": "businessKey",
                         "값": em, "근거": f"메일 발신 {r['n']}회", "파일": str(names[hit])})
        else:
            new_people.append({"name": r["name"], "email": em, "org": r["org"],
                               "n": r["n"], "proj": sorted(r["proj"])})
    for dom, r in sorted(orgs.items(), key=lambda x: -x[1]["n"]):
        hit = match_name(r["name"], names)
        if hit:
            cur = fm.get(hit, {})
            if (cur.get("businessKey") or "").strip(' "'):
                continue
            fill.append({"class": cur.get("_class"), "name": hit, "필드": "businessKey",
                         "값": dom, "근거": f"메일 도메인 {r['n']}회", "파일": str(names[hit])})
        else:
            new_orgs.append({"name": r["name"], "domain": dom, "n": r["n"],
                             "proj": sorted(r["proj"])})

    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / f"키채움_{today}.json").write_text(
        json.dumps(fill, ensure_ascii=False, indent=1), encoding="utf-8")

    L = [f"# 온톨로지 성장 제안 — {today}", "",
         "메일 분류 결과에서 거둔 것입니다. **이 문서는 제안이고, SSOT 에는 아무것도 쓰지 않았습니다.**",
         "기존 엔티티의 빈 칸을 채우는 것과 새 엔티티를 만드는 것을 갈라 두었습니다.", "",
         f"- 기존 엔티티 빈 키 채움 후보: **{len(fill)}건**",
         f"- 새 사람 제안: **{len(new_people)}명**",
         f"- 새 조직 제안: **{len(new_orgs)}곳**",
         f"- 온톨로지 현재: 사람·조직·고객·과제 합 {len(names)}건", "",
         "## 1. 빈 키 채움 (되돌릴 수 있고 충돌이 없습니다)", ""]
    if fill:
        L += ["| 분류 | 이름 | 채울 값 | 근거 |", "|---|---|---|---|"]
        L += [f"| {x['class']} | {x['name']} | `{x['값']}` | {x['근거']} |" for x in fill[:80]]
        if len(fill) > 80:
            L.append(f"\n_외 {len(fill) - 80}건은 `키채움_{today}.json` 참조_")
    else:
        L.append("_해당 없음._")

    L += ["", "## 2. 새 사람 제안", "",
          "온톨로지에 없는 이름입니다. **표기가 달라 못 찾은 것일 수 있으니** 합칠지 새로 만들지 사람이 정합니다.", ""]
    if new_people:
        L += ["| 이름 | 메일 | 소속(추정) | 등장 | 관련 과제 |", "|---|---|---|---|---|"]
        L += [f"| {p['name']} | `{p['email']}` | {p['org'] or '-'} | {p['n']}회 | "
              f"{', '.join(p['proj']) or '-'} |" for p in new_people[:60]]
        if len(new_people) > 60:
            L.append(f"\n_외 {len(new_people) - 60}명_")
    else:
        L.append("_해당 없음._")

    L += ["", "## 3. 새 조직 제안", ""]
    if new_orgs:
        L += ["| 조직 | 도메인 | 등장 | 관련 과제 |", "|---|---|---|---|"]
        L += [f"| {o['name']} | `{o['domain']}` | {o['n']}회 | "
              f"{', '.join(o['proj']) or '-'} |" for o in new_orgs[:60]]
        if len(new_orgs) > 60:
            L.append(f"\n_외 {len(new_orgs) - 60}곳_")
    else:
        L.append("_해당 없음._")

    L += ["", "## 반영하는 법", "",
          "1. 위 1번 표는 **기존 파일의 빈 `businessKey` 에 값만 넣는 것**입니다. 기존 값은 덮지 않습니다",
          "2. 2·3번은 새 파일을 만드는 일이라 중복 위험이 있습니다. 비슷한 이름이 이미 있는지 보고 정하십시오",
          "3. 도메인이 붙으면 그 조직의 메일이 다음 회차부터 과제에 자동으로 귀속됩니다", ""]

    (OUT / f"제안_{today}.md").write_text("\n".join(L), encoding="utf-8")
    print(f"· 빈 키 채움 {len(fill)} · 새 사람 {len(new_people)} · 새 조직 {len(new_orgs)}")
    print(f"  → {(OUT / f'제안_{today}.md').relative_to(PROJ)}")
    print("  (SSOT 에는 아무것도 쓰지 않았습니다)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
