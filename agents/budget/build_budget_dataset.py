#!/usr/bin/env python3
"""예산 현황 스냅샷(원장 xlsx 의 사본) → **정규 데이터셋 + 히스토리 + SSOT 게시**.

    python3 scripts/build_budget_dataset.py

엑셀 양식은 중요하지 않다(운영자 2026-08-27). 목적은 수많은 과제의 예산을 다루는
**시스템**이다 — 집행·회계처리·증빙에서 그대로 쓰이는 데이터.

    원장(xlsx, {{MEMBER_A}} 주단위)          ← 신뢰 원장. 우리는 읽기만 한다
      → data/예산현황/<날짜>.json      원문 스냅샷 (행 보존, 이력의 원본)
      → data/dataset/budget_<날짜>.json 정규 레코드: 과제 → 비목·세목 라인
      → data/dataset/history.jsonl     버전 간 변화 (편성 변경·집행 발생)
      → SSOT 20_예산/10_시스템등록_예산현황/   사람이 읽는 md + 기계가 읽는 data/

파서 원칙 — **확신 있는 것만 해석하고, 못 읽은 행 수를 숨기지 않는다.**
coverage 를 과제마다 기록한다. 원문 스냅샷이 항상 남으므로 해석 실패는 복구 가능하다.
"""
from __future__ import annotations

import datetime as dt
import json
import re
import shutil
from pathlib import Path

PROJ = Path(__file__).resolve().parents[1]
SNAP = PROJ / "data" / "예산현황"
DS = PROJ / "data" / "dataset"
HOME = Path.home()
# 2026-09-05: 마운트 쓰기는 유지하되 **도착을 API 해시로 검증**한다(lib/ssot_io.verify_dir).
# 업로드 rc=0 은 도착의 증거가 아니다 — "☁ 반영"이 거짓이던 지점.
SSOT_OUT = (HOME / "{{MOUNT_REL}}"
            / "공유 드라이브/{{SSOT_DRIVE}}/{{DEPT_ROOT}}/20_예산/10_시스템등록_예산현황")

# 시트 → 과제 ID. 원장 시트명이 바뀌면 여기만 고친다.
SHEET_MAP = {
    "1.팁스 글로벌트랙": "PRJ-N001",
    "12.팁스 연계(창업사업화)": "PRJ-N012a",
    "12.팁스 연계(해외마케팅)": "PRJ-N012b",
    "16. AI바우처 일반(NIPA)": "PRJ-N016",
    "21. AX SPRINT 사업(복지분야)": "PRJ-N021",
    "3. 질병청 공간전사체": "PRJ-N003",
    "SVC seoul": "PRJ-E8D66D",
    "2.자폐성 발달장애": "PRJ-N002",
    "14.첨단 GPU": "PRJ-N014",
}
SUMMARY_KEYS = ["지원금", "자부담_현금", "자부담_현물", "자부담_합계", "총계"]


def num(c):
    if isinstance(c, (int, float)):
        return float(c)
    return None


def txt(c) -> str:
    return re.sub(r"\s+", " ", str(c or "")).strip()


def parse_summary(rows) -> dict | None:
    """「예산/사용/차액」 3행 블록. #REF! 는 None — 계산 안 된 값은 값이 아니다."""
    out = {}
    for r in rows:
        head = txt(r[0] if r else "")
        if head in ("예산", "사용", "차액"):
            vals = [(num(c) if c != "#REF!" else None)
                    for c in r[1:] if c not in ("", None)]
            keys = SUMMARY_KEYS if len(vals) >= 5 else \
                (["지원금", "자부담_합계", "총계"] if len(vals) == 3 else
                 [f"v{i}" for i in range(len(vals))])
            out[head] = dict(zip(keys, vals))
    return out or None


def header_cols(rows) -> tuple[int, dict] | None:
    """세부내역 헤더 위치와 열 배치. 헤더가 두 행에 걸치므로(합계 밑에 현금/현물/계/사용)
    두 행을 합쳐 토큰을 찾는다."""
    for i, r in enumerate(rows):
        joined = [txt(c) for c in r]
        if any("단가" in c for c in joined) and any("회수" in c for c in joined):
            nxt = [txt(c) for c in (rows[i + 1] if i + 1 < len(rows) else [])]
            merged = [(a + " " + (nxt[j] if j < len(nxt) else "")).strip()
                      for j, a in enumerate(joined)]
            cols = {}
            for j, tok in enumerate(merged):
                if "단가" in tok:
                    cols.setdefault("단가", j)
                elif "회수" in tok:
                    cols.setdefault("회수", j)
                elif "사용" in tok:
                    cols.setdefault("집행", j)
                elif "현물" in tok:
                    cols.setdefault("현물", j)
                elif tok == "계" or tok.endswith(" 계"):
                    cols.setdefault("계", j)
                elif "합계" in tok or "현금" in tok:
                    cols.setdefault("현금", j)
            start = i + (2 if any(nxt) and not any(txt(c) for c in (rows[i + 1][:2] if i + 1 < len(rows) else [])) else 1)
            return start, cols
    return None


def parse_lines(rows) -> tuple[list, int]:
    hc = header_cols(rows)
    if not hc:
        return [], 0
    start, cols = hc
    lines, skipped = [], 0
    cur_bimok = ""
    for r in rows[start:]:
        c0, c1 = txt(r[0] if len(r) > 0 else ""), txt(r[1] if len(r) > 1 else "")
        if c0.startswith(("총계", "총 합계")):
            break
        label_cells = [txt(c) for c in r[:6]]
        nums = {k: num(r[j]) if j < len(r) and r[j] != "#REF!" else None
                for k, j in cols.items()}
        if c0:
            cur_bimok = c0.replace("\n", " ")
        semok = c1.replace("\n", " ")
        naeyeok = next((c for c in label_cells[2:] if c), "")
        is_subtotal = "소계" in (c0 + semok)
        has_value = any(v is not None for v in nums.values())
        if not has_value and not naeyeok:
            skipped += 1
            continue
        if is_subtotal:
            continue                       # 소계는 라인의 합 — 이중계상 방지
        lines.append({
            "비목": cur_bimok, "세목": semok, "내역": naeyeok[:80],
            "단가": nums.get("단가"), "회수": nums.get("회수"),
            "편성_현금": nums.get("현금"), "편성_현물": nums.get("현물"),
            "편성_계": nums.get("계") if nums.get("계") is not None else nums.get("현금"),
            "집행": nums.get("집행"),
        })
    return lines, skipped


def build_one(snap: dict) -> dict:
    projects = []
    for sheet, rows in snap["sheets"].items():
        key = sheet.strip()
        pid = SHEET_MAP.get(key) or SHEET_MAP.get(key.rstrip())
        refs = sum(1 for r in rows for c in r if c == "#REF!")
        period = ""
        for r in rows[:3]:
            for c in r:
                if isinstance(c, str) and re.search(r"20\d\d\s*[.~]", c):
                    period = txt(c)
        lines, skipped = parse_lines(rows)
        p = {
            "project_id": pid, "sheet": key, "period": period,
            "summary": parse_summary(rows), "lines": lines,
            "coverage": {"rows": len(rows), "lines": len(lines), "skipped": skipped},
            "warnings": ([f"#REF! {refs}곳 — 원장 수식 깨짐"] if refs else [])
                        + (["시트 비어 있음"] if len(rows) <= 1 else []),
        }
        for ln in lines:                    # 잔액은 계산값 — 원장에 없어도 만들 수 있다
            if ln["편성_계"] is not None and ln["집행"] is not None:
                ln["잔액"] = ln["편성_계"] - ln["집행"]
        projects.append(p)
    return {"as_of": snap["date"], "source_file": snap["source_file"],
            "projects": projects}


def diff(prev: dict, cur: dict) -> list:
    """버전 간 변화. 편성이 바뀌면 협약·비목 변경의 신호, 집행이 바뀌면 지출 발생."""
    out = []
    P = {p["sheet"]: p for p in prev["projects"]}
    for p in cur["projects"]:
        q = P.get(p["sheet"])
        if not q:
            out.append({"과제": p["sheet"], "종류": "과제 신규", "이후": p["period"]})
            continue
        old = {(l["비목"], l["세목"], l["내역"][:24]): l for l in q["lines"]}
        for l in p["lines"]:
            k = (l["비목"], l["세목"], l["내역"][:24])
            o = old.pop(k, None)
            if o is None:
                out.append({"과제": p["sheet"], "종류": "라인 신규",
                            "라인": " / ".join(k), "이후": l["편성_계"]})
                continue
            for field, kind in (("편성_계", "편성 변경"), ("집행", "집행 변화")):
                a, b = o.get(field), l.get(field)
                if a != b and not (a is None and b is None):
                    out.append({"과제": p["sheet"], "종류": kind,
                                "라인": " / ".join(k), "이전": a, "이후": b,
                                "증감": (b - a) if (a is not None and b is not None) else None})
        for k in old:
            out.append({"과제": p["sheet"], "종류": "라인 소멸", "라인": " / ".join(k)})
    return out


def fmt(v):
    return f"{v:,.0f}" if isinstance(v, (int, float)) else ("—" if v is None else str(v))


def publish(ds: dict, history: list) -> None:
    if not SSOT_OUT.parent.exists():
        print("  ⚠ SSOT 예산 폴더 없음 — 게시 생략")
        return
    if SSOT_OUT.exists():
        shutil.rmtree(SSOT_OUT)
    (SSOT_OUT / "과제").mkdir(parents=True)
    (SSOT_OUT / "data").mkdir()

    total_rows = []
    for p in sorted(ds["projects"], key=lambda x: x["sheet"]):
        s = (p.get("summary") or {}).get("예산") or {}
        spent = sum(l["집행"] for l in p["lines"] if l.get("집행"))
        warn = " · ".join(p["warnings"]) if p["warnings"] else ""
        total_rows.append(
            f"| {p['sheet']} | {p.get('project_id') or '—'} | {p['period'] or '—'} "
            f"| {fmt(s.get('총계'))} | {fmt(s.get('지원금'))} | {fmt(spent) if spent else '—'} "
            f"| {warn or '✅'} |")

        # 과제별 md
        L = [f"# {p['sheet']}", "",
             f"- 과제: **{p.get('project_id') or '미매핑'}** · 기간 {p['period'] or '—'} · 기준일 {ds['as_of']}",
             f"- 원장: `{ds['source_file']}` ({{MEMBER_A}} 주단위 갱신 — **이 파일이 정본, 이 문서는 생성물**)", ""]
        if p["warnings"]:
            L += [f"> ⚠ {w}" for w in p["warnings"]] + [""]
        if p.get("summary"):
            L += ["## 재원 요약", "", "| 구분 | " + " | ".join(SUMMARY_KEYS) + " |",
                  "|" + "---|" * (len(SUMMARY_KEYS) + 1)]
            for k in ("예산", "사용", "차액"):
                row = p["summary"].get(k) or {}
                L.append(f"| {k} | " + " | ".join(fmt(row.get(x)) for x in SUMMARY_KEYS) + " |")
            L.append("")
        if p["lines"]:
            L += ["## 비목·세목 편성과 집행", "",
                  "| 비목 | 세목 | 내역 | 단가 | 회수 | 편성(계) | 집행 | 잔액 |",
                  "|---|---|---|---:|---:|---:|---:|---:|"]
            for l in p["lines"]:
                L.append(f"| {l['비목']} | {l['세목']} | {l['내역']} | {fmt(l['단가'])} "
                         f"| {fmt(l['회수'])} | {fmt(l['편성_계'])} | {fmt(l.get('집행'))} "
                         f"| {fmt(l.get('잔액'))} |")
            cov = p["coverage"]
            L += ["", f"*해석 라인 {cov['lines']}개 · 원문 {cov['rows']}행 — 전체 원문은 "
                      f"`data/budget_{ds['as_of']}.json` 및 원장 스냅샷에 보존*"]
        else:
            L += ["*세부내역을 해석하지 못했다 — 원문 스냅샷 참조*"]
        slug = re.sub(r"[^\w가-힣. -]", "", p["sheet"])
        (SSOT_OUT / "과제" / f"{slug}.md").write_text("\n".join(L) + "\n", encoding="utf-8")

    # 총괄
    head = ["---", f'source_date: "{ds["as_of"]}"',
            f"generated: {dt.datetime.now():%Y-%m-%dT%H:%M:%S+0900}", "---", "",
            f"# 과제 예산 총괄 — 시스템 등록 기준 {ds['as_of']}", "",
            "| 과제 | ID | 기간 | 편성 총계 | 지원금 | 집행(파악분) | 상태 |",
            "|---|---|---|---:|---:|---:|---|"] + total_rows
    (SSOT_OUT / "00_총괄.md").write_text("\n".join(head) + "\n", encoding="utf-8")

    # 변경 이력
    H = ["# 변경 이력 — 원장 버전 간 비교", "",
         "편성이 바뀌면 협약·비목 변경의 신호, 집행이 바뀌면 지출 발생이다.", ""]
    for h in history:
        H.append(f"## {h['from']} → {h['to']} ({h['n']}건)")
        H += ["", "| 과제 | 종류 | 라인 | 이전 | 이후 | 증감 |", "|---|---|---|---:|---:|---:|"]
        for e in h["entries"]:
            H.append(f"| {e.get('과제','')} | {e.get('종류','')} | {e.get('라인','')} "
                     f"| {fmt(e.get('이전'))} | {fmt(e.get('이후'))} | {fmt(e.get('증감'))} |")
        H.append("")
    (SSOT_OUT / "90_변경이력.md").write_text("\n".join(H) + "\n", encoding="utf-8")

    # 기계용 데이터
    for f in DS.glob("budget_*.json"):
        shutil.copy2(f, SSOT_OUT / "data" / f.name)
    if (DS / "history.jsonl").exists():
        shutil.copy2(DS / "history.jsonl", SSOT_OUT / "data" / "history.jsonl")

    (SSOT_OUT / "_INDEX.md").write_text("\n".join([
        "---", 'path: "{{DEPT_ROOT}}/20_예산/10_시스템등록_예산현황"',
        f"generated: {dt.datetime.now():%Y-%m-%dT%H:%M:%S+0900}",
        f'source_date: "{ds["as_of"]}"', 'stale_after: "P7D"', "---", "",
        "## 이 폴더는", "",
        "과제 예산의 **정규 데이터셋** — 집행·회계처리·증빙에서 그대로 쓰는 데이터다.",
        "원장(신뢰의 출발점)은 U 드라이브 `2-7. 각 사업 예산 현황` 의 최신 xlsx 이며",
        "({{MEMBER_A}} 주단위 갱신), 자동 사이클이 새 버전을 감지하면 여기 전체를 다시 만든다.", "",
        "**여기 파일은 전부 생성물이다 — 손으로 고치지 않는다.** 고칠 것은 원장 xlsx.", "",
        "| 파일 | 내용 |", "|---|---|",
        "| `00_총괄.md` | 전 과제 편성·집행 한 장 |",
        "| `과제/*.md` | 과제별 비목·세목 편성/집행/잔액 |",
        "| `90_변경이력.md` | 원장 버전 간 편성 변경·집행 발생 |",
        "| `data/budget_*.json` | 기계용 정규 레코드 (회계·증빙 자동화의 입력) |",
        "| `data/history.jsonl` | 변경 이력 원본 |",
    ]) + "\n", encoding="utf-8")
    print(f"  → SSOT 게시: 과제 {len(ds['projects'])}건 · 이력 {sum(h['n'] for h in history)}건")


def main() -> int:
    snaps = sorted(SNAP.glob("2*.json"))
    if not snaps:
        print("스냅샷 없음")
        return 2
    DS.mkdir(parents=True, exist_ok=True)
    built = []
    for f in snaps:
        ds = build_one(json.loads(f.read_text(encoding="utf-8")))
        (DS / f"budget_{ds['as_of']}.json").write_text(
            json.dumps(ds, ensure_ascii=False), encoding="utf-8")
        built.append(ds)
        cov = sum(p["coverage"]["lines"] for p in ds["projects"])
        print(f"· {ds['as_of']}: 과제 {len(ds['projects'])} · 해석 라인 {cov}")

    history = []
    for a, b in zip(built, built[1:]):
        entries = diff(a, b)
        history.append({"from": a["as_of"], "to": b["as_of"],
                        "n": len(entries), "entries": entries})
    (DS / "history.jsonl").write_text(
        "".join(json.dumps(h, ensure_ascii=False) + "\n" for h in history),
        encoding="utf-8")

    publish(built[-1], history)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
