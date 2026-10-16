#!/usr/bin/env python3
"""전 부서 주간업무보고를 컨플루언스에서 수집하고 구조화한다.

주간보고 본문은 페이지가 아니라 PPTX/PDF 첨부에 들어 있다.
따라서 페이지 조회 → 첨부 내려받기 → 표 파싱까지 한 번에 수행한다.

    python3 collect_weekly.py                 # 최근 4주
    python3 collect_weekly.py --weeks 12      # 최근 12주
    python3 collect_weekly.py --since 2026-06-01

산출물:
    data/pages/<id>.json          페이지 메타 + storage HTML
    data/attachments/<id>/<파일>   원본 첨부
    data/parsed/<id>.json         파싱된 보고 표
    data/weekly_index.json        부서 × 주차 색인 (대시보드 입력)
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from confluence_client import ConfluenceError, get_client  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
PAGES, ATT, PARSED = DATA / "pages", DATA / "attachments", DATA / "parsed"
INDEX = DATA / "weekly_index.json"
ORG_MAP = DATA / "부서_정규화.json"

# 주간보고 페이지 제목 규칙: [<조직> 주간업무보고] 260811 / [<조직> 주간보고] 260811
TITLE_RE = re.compile(r"^\[(?P<org>[^\]]+?)\s*주간\s*(?:업무)?보고\]\s*(?P<yymmdd>\d{6})\s*$")


def log(msg: str) -> None:
    print(f"[{dt.datetime.now():%H:%M:%S}] {msg}", flush=True)


# --------------------------------------------------------------------------- 수집

def search_weekly_pages(cli, since: dt.date) -> list[dict]:
    """lastmodified 기준으로 주간보고 페이지를 찾는다."""
    cql = f'type=page and lastmodified >= "{since:%Y/%m/%d}" order by lastmodified desc'
    from urllib.parse import quote
    out, cursor = [], None
    while True:
        path = (f"/wiki/rest/api/content/search?cql={quote(cql)}&limit=100"
                "&expand=version,space")
        if cursor:
            path += f"&cursor={cursor}"
        j = cli.get_json(path)
        out.extend(j.get("results", []))
        nxt = (j.get("_links") or {}).get("next")
        if not nxt:
            break
        m = re.search(r"cursor=([^&]+)", nxt)
        if not m:
            break
        cursor = m.group(1)
    return out


def classify(title: str) -> tuple[str, str, str] | None:
    """제목 → (조직, 주차, 원본 날짜).

    부서마다 페이지 날짜 표기가 하루 이틀 다르다(클라우드는 월요일 260727,
    나머지는 화요일 260728). 전사 주간회의가 화요일이므로 **그 주의 화요일**로
    정규화해야 부서 간 비교가 성립한다.
    """
    m = TITLE_RE.match(title.strip())
    if not m:
        return None
    yy = m.group("yymmdd")
    raw = f"20{yy[0:2]}-{yy[2:4]}-{yy[4:6]}"
    d = dt.date.fromisoformat(raw)
    tuesday = d - dt.timedelta(days=(d.weekday() - 1) % 7)
    return m.group("org").strip(), tuesday.isoformat(), raw


def fetch_page(cli, pid: str) -> dict:
    j = cli.get_json(f"/wiki/rest/api/content/{pid}?expand=body.storage,version,space")
    rec = {
        "id": j["id"],
        "title": j["title"],
        "updated": (j.get("version") or {}).get("when"),
        "space": (j.get("space") or {}).get("key"),
        "html": ((j.get("body") or {}).get("storage") or {}).get("value", ""),
    }
    (PAGES / f"{pid}.json").write_text(
        json.dumps(rec, ensure_ascii=False, indent=1), encoding="utf-8")
    return rec


def week_tokens(week: str) -> set[str]:
    """해당 주차 보고서 파일명에 들어갈 법한 yymmdd 후보.

    주간회의는 화요일이지만 파일은 직전 월요일~금요일 날짜로 저장되는 경우가 많다
    (예: 260804 페이지의 첨부가 `주간업무보고_260803.pptx`).
    """
    d = dt.date.fromisoformat(week)
    return {(d + dt.timedelta(days=k)).strftime("%y%m%d") for k in range(-7, 2)}


def fetch_attachments(cli, pid: str, week: str,
                      want=(".pptx", ".pdf")) -> list[Path]:
    """해당 주차 첨부만 내려받는다.

    페이지에는 과거 주간보고가 수십 개 누적돼 있어 전부 받으면 실행이 끝나지 않는다.
    파일명의 날짜 토큰으로 먼저 거르고, 못 찾으면 최근 수정본 1개만 받는다.
    """
    j = cli.get_json(f"/wiki/rest/api/content/{pid}"
                     "/child/attachment?limit=100&expand=version")
    cands = [a for a in j.get("results", []) if a["title"].lower().endswith(want)]
    if not cands:
        return []

    toks = week_tokens(week)
    hit = []
    for a in cands:
        found = sorted(t for t in toks if t in a["title"])
        if found:
            hit.append((found[-1], a))          # 파일명 안의 가장 늦은 날짜
    if hit:
        best = max(t for t, _ in hit)           # 주차에 가장 가까운 날짜만 채택
        hit = [a for t, a in hit if t == best]
    else:
        hit = sorted(cands, key=lambda a: (a.get("version") or {}).get("when") or "",
                     reverse=True)[:1]
        log(f"  주차 일치 첨부 없음 → 최근본 사용: {hit[0]['title']}")

    outdir, saved = ATT / pid, []
    for a in hit:
        dest = outdir / a["title"]
        size = int((a.get("extensions") or {}).get("fileSize") or -1)
        if dest.exists() and dest.stat().st_size == size:
            saved.append(dest)
            continue
        dl = (a.get("_links") or {}).get("download")
        if not dl:
            continue
        try:
            blob = cli.get_bytes("/wiki" + dl if dl.startswith("/") else dl)
        except ConfluenceError as ex:
            log(f"  첨부 실패 {a['title']}: {ex}")
            continue
        outdir.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(blob)
        saved.append(dest)
    return saved


# --------------------------------------------------------------------------- 파싱

def parse_pptx(path: Path) -> list[list[str]]:
    """PPTX 안의 첫 번째 큰 표를 행 리스트로 돌려준다."""
    try:
        from pptx import Presentation
    except ImportError:
        log("  python-pptx 미설치 — pip install python-pptx")
        return []
    rows: list[list[str]] = []
    prs = Presentation(str(path))
    for slide in prs.slides:
        for sh in slide.shapes:
            if not sh.has_table:
                continue
            t = sh.table
            got = []
            for r in t.rows:
                cells = [c.text.replace("\n", " / ").strip() for c in r.cells]
                if any(cells):
                    got.append(cells)
            if len(got) > len(rows):
                rows = got
    return rows


def parse_pdf(path: Path) -> list[list[str]]:
    """PDF는 표 구조 복원이 어려워 텍스트만 줄 단위로 남긴다."""
    import shutil
    import subprocess
    if not shutil.which("pdftotext"):
        return []
    r = subprocess.run(["pdftotext", "-layout", str(path), "-"],
                       capture_output=True, timeout=120)
    txt = r.stdout.decode("utf-8", "replace")
    return [[ln.strip()] for ln in txt.splitlines() if ln.strip()]


HEADER_HINTS = ("구분", "PJT", "전주", "금주", "이슈")


def normalize_rows(rows: list[list[str]]) -> dict:
    """헤더 행을 찾아 항목 딕셔너리 리스트로 만든다."""
    if not rows:
        return {"header": [], "items": []}
    hidx = 0
    for i, r in enumerate(rows[:4]):
        joined = " ".join(r)
        if sum(h in joined for h in HEADER_HINTS) >= 3:
            hidx = i
            break
    header = [c.strip() for c in rows[hidx]]
    items = []
    for r in rows[hidx + 1:]:
        if not any(c.strip() for c in r):
            continue
        items.append({header[i] if i < len(header) else f"col{i}": v
                      for i, v in enumerate(r)})
    return {"header": header, "items": items}


# --------------------------------------------------------------------------- 메인

def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--weeks", type=int, default=4)
    ap.add_argument("--since")
    ap.add_argument("--no-browser", action="store_true",
                    help="API 토큰만 사용 (스케줄 실행용)")
    args = ap.parse_args()

    since = (dt.date.fromisoformat(args.since) if args.since
             else dt.date.today() - dt.timedelta(weeks=args.weeks))

    for d in (PAGES, ATT, PARSED):
        d.mkdir(parents=True, exist_ok=True)

    try:
        cli = get_client(allow_browser=not args.no_browser)
    except ConfluenceError as e:
        log(f"인증 실패: {e}")
        return 2
    log(f"인증 방식: {cli.mode} · 수집 기준일 {since}")

    pages = search_weekly_pages(cli, since)
    weekly = [(p, classify(p["title"])) for p in pages]
    weekly = [(p, c) for p, c in weekly if c]
    log(f"페이지 {len(pages)}건 중 주간보고 {len(weekly)}건")

    index = json.loads(INDEX.read_text(encoding="utf-8")) if INDEX.exists() else {}
    orgmap = json.loads(ORG_MAP.read_text(encoding="utf-8")) if ORG_MAP.exists() else {}

    for p, (org_raw, week, raw_date) in weekly:
        pid = p["id"]
        log(f"· {org_raw} {week} (id={pid})")
        rec = fetch_page(cli, pid)

        # 「전사」는 부서별 링크만 모아둔 색인 페이지다. 첨부는 다른 부서 것이 섞여 있어
        # 보고서로 파싱하면 안 된다. 제출 현황 확인용으로만 남긴다.
        if org_raw == "전사":
            parsed = {"header": [], "items": [], "source_file": None, "format": "index"}
            files = []
        else:
            files = fetch_attachments(cli, pid, raw_date)
            parsed = {"header": [], "items": [], "source_file": None, "format": "none"}
            for f in sorted(files, key=lambda x: (x.suffix.lower() != ".pptx", x.name)):
                if f.suffix.lower() == ".pptx":
                    rows, fmt = parse_pptx(f), "table"
                else:
                    rows, fmt = parse_pdf(f), "text"
                if rows:
                    parsed = (normalize_rows(rows) if fmt == "table"
                              else {"header": [], "items": [],
                                    "text": [r[0] for r in rows]})
                    parsed["source_file"] = f.name
                    parsed["format"] = fmt
                    break

        out = {
            "page_id": pid, "title": rec["title"], "updated": rec["updated"],
            "org_raw": org_raw, "org": orgmap.get(org_raw, {}).get("정규명", org_raw),
            "week": week, "page_date": raw_date, "url": f"{{WIKI_BASE}}/wiki/spaces/"
                                 f"{rec['space']}/pages/{pid}",
            **parsed,
        }
        (PARSED / f"{pid}.json").write_text(
            json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
        index[pid] = {k: out[k] for k in
                      ("org", "org_raw", "week", "title", "updated", "url", "source_file")}
        index[pid]["item_count"] = len(out["items"])
        index[pid]["format"] = out.get("format")
        log(f"  → 항목 {len(out['items'])}건 ({parsed['source_file']})")

    INDEX.write_text(json.dumps(index, ensure_ascii=False, indent=1), encoding="utf-8")
    orgs = sorted({v["org"] for v in index.values()})
    log(f"완료 · 색인 {len(index)}건 · 부서 {len(orgs)}개: {', '.join(orgs)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
