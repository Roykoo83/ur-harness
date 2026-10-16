#!/usr/bin/env python3
"""Confluence Cloud 전체 수집기 — 페이지 본문·첨부·링크를 깊이 제한 없이 내려받는다.

기존 PoC(2026-06)는 `metadata_and_direct_child_pages_only_no_body_content` 범위여서
본문이 없고 2단계에서 멈췄다. 이 도구는 그 두 한계를 모두 없앤다.

인증 (둘 다 필요):
    export CONFLUENCE_EMAIL='{{OPERATOR_ACCOUNT}}'
    export CONFLUENCE_API_TOKEN='...'      # id.atlassian.com/manage-profile/security/api-tokens

사용:
    # 스페이스 목록만 보기
    confluence-sync.py --list-spaces
    # 특정 루트 페이지 아래 전부
    confluence-sync.py --root 7700667 --out ./out
    # 제목으로 찾아서 그 아래 전부
    confluence-sync.py --search "주간업무보고" --out ./out
    # 스페이스 통째로
    confluence-sync.py --space xTvtSnm1zBv3 --out ./out

결과:
    out/pages/<id>_<제목>.md        본문 (Markdown)
    out/attachments/<id>/<파일명>   첨부 원본 (pptx 등)
    out/manifest.json               계층·링크·첨부 인덱스
"""
import argparse
import base64
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

BASE = "{{WIKI_BASE}}"
PAGE_LIMIT = 100


def auth_header():
    email = os.environ.get("CONFLUENCE_EMAIL")
    token = os.environ.get("CONFLUENCE_API_TOKEN")
    if not (email and token):
        sys.exit("CONFLUENCE_EMAIL 과 CONFLUENCE_API_TOKEN 환경변수가 필요합니다.\n"
                 "토큰 발급: https://id.atlassian.com/manage-profile/security/api-tokens")
    raw = f"{email}:{token}".encode()
    return "Basic " + base64.b64encode(raw).decode()


def api(path, params=None, raw=False, retries=3):
    url = BASE + path
    if params:
        url += ("&" if "?" in url else "?") + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={
        "Authorization": auth_header(),
        "Accept": "*/*" if raw else "application/json",
    })
    for attempt in range(retries):
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                return r.read() if raw else json.loads(r.read())
        except urllib.error.HTTPError as e:
            if e.code in (429, 502, 503) and attempt < retries - 1:
                time.sleep(2 ** attempt * 2)
                continue
            if e.code == 401:
                sys.exit("인증 실패(401). 이메일/토큰을 확인하세요.")
            raise
        except urllib.error.URLError:
            if attempt < retries - 1:
                time.sleep(2 ** attempt)
                continue
            raise


def paged(path, params=None):
    """v2 API cursor pagination."""
    params = dict(params or {})
    params.setdefault("limit", PAGE_LIMIT)
    while True:
        data = api(path, params)
        yield from data.get("results", [])
        nxt = (data.get("_links") or {}).get("next")
        if not nxt:
            return
        # next 는 '/wiki/api/v2/...?cursor=...' 형태
        path, _, qs = nxt.partition("?")
        params = dict(urllib.parse.parse_qsl(qs))


def safe(name, limit=90):
    name = re.sub(r'[/\\:*?"<>|\n\r\t]', "_", name).strip()
    return (name[:limit] or "untitled").rstrip(". ")


def storage_to_markdown(html):
    """Confluence storage format -> 읽을 수 있는 Markdown (표·링크·첨부 보존)."""
    import lxml.html as LH
    from lxml import etree

    if not html:
        return ""
    # 네임스페이스 선언을 붙여줘야 ac:/ri: 태그가 파싱된다
    wrapped = (
        '<div xmlns:ac="http://atlassian.com/content" '
        'xmlns:ri="http://atlassian.com/resource/identifier">' + html + "</div>"
    )
    try:
        root = etree.fromstring(wrapped.encode(), etree.HTMLParser())
    except Exception:
        root = LH.fromstring(html)

    out = []

    def text_of(el):
        return " ".join("".join(el.itertext()).split())

    def walk(el):
        tag = etree.QName(el).localname.lower() if isinstance(el.tag, str) else ""
        if tag in ("h1", "h2", "h3", "h4", "h5"):
            out.append("#" * int(tag[1]) + " " + text_of(el))
        elif tag == "table":
            rows = []
            for tr in el.iter():
                if etree.QName(tr).localname.lower() != "tr":
                    continue
                cells = [text_of(td) for td in tr
                         if etree.QName(td).localname.lower() in ("td", "th")]
                if cells:
                    rows.append(cells)
            if rows:
                w = max(len(r) for r in rows)
                rows = [r + [""] * (w - len(r)) for r in rows]
                out.append("| " + " | ".join(rows[0]) + " |")
                out.append("|" + "|".join(["---"] * w) + "|")
                for r in rows[1:]:
                    out.append("| " + " | ".join(r) + " |")
            return
        elif tag == "li":
            out.append("- " + text_of(el))
            return
        elif tag == "p":
            t = text_of(el)
            if t:
                out.append(t)
            return
        for child in el:
            walk(child)

    walk(root)

    # 첨부·링크 흔적을 별도로 기록 (본문 텍스트만으로는 사라지므로)
    refs = []
    for m in re.finditer(r'ri:filename="([^"]+)"', html):
        refs.append(f"[첨부] {m.group(1)}")
    for m in re.finditer(r'ri:content-title="([^"]+)"', html):
        refs.append(f"[페이지링크] {m.group(1)}")
    for m in re.finditer(r'href="(https?://[^"]+)"', html):
        refs.append(f"[외부링크] {m.group(1)}")
    if refs:
        out.append("")
        out.append("## 참조")
        out += sorted(set(refs))

    return "\n\n".join(x for x in out if x.strip())


def fetch_page(pid):
    return api(f"/wiki/api/v2/pages/{pid}", {"body-format": "storage"})


def children_of(pid):
    return list(paged(f"/wiki/api/v2/pages/{pid}/children"))


def attachments_of(pid):
    return list(paged(f"/wiki/api/v2/pages/{pid}/attachments"))


def walk_tree(root_id, out_dir, seen=None, depth=0, max_depth=None, stats=None):
    """깊이 제한 없이 전 하위를 순회한다 (max_depth 를 주면 그때만 제한)."""
    seen = seen if seen is not None else set()
    stats = stats if stats is not None else {"pages": 0, "attachments": 0, "bytes": 0}
    if root_id in seen:
        return []
    seen.add(root_id)

    page = fetch_page(root_id)
    title = page.get("title", "")
    body = ((page.get("body") or {}).get("storage") or {}).get("value", "")

    pdir = out_dir / "pages"
    pdir.mkdir(parents=True, exist_ok=True)
    md = [f"# {title}", "",
          f"- page id: `{root_id}`",
          f"- url: {BASE}/wiki/spaces/{(page.get('spaceId') or '')}/pages/{root_id}",
          f"- version: {(page.get('version') or {}).get('number')} "
          f"({(page.get('version') or {}).get('createdAt', '')[:10]})",
          f"- depth: {depth}", "", "---", "", storage_to_markdown(body)]
    (pdir / f"{root_id}_{safe(title)}.md").write_text("\n".join(md), encoding="utf-8")
    stats["pages"] += 1

    atts = []
    for a in attachments_of(root_id):
        fname = a.get("title") or a.get("id")
        dl = (a.get("_links") or {}).get("download") or a.get("downloadLink")
        rec = {"id": a.get("id"), "title": fname,
               "mediaType": a.get("mediaType"), "size": a.get("fileSize")}
        if dl:
            adir = out_dir / "attachments" / str(root_id)
            adir.mkdir(parents=True, exist_ok=True)
            target = adir / safe(fname)
            if not target.exists():
                try:
                    blob = api("/wiki" + dl if dl.startswith("/") else dl, raw=True)
                    target.write_bytes(blob)
                    stats["attachments"] += 1
                    stats["bytes"] += len(blob)
                except Exception as e:            # noqa: BLE001
                    rec["error"] = str(e)
            rec["path"] = str(target.relative_to(out_dir))
        atts.append(rec)

    node = {"id": root_id, "title": title, "depth": depth,
            "version": (page.get("version") or {}).get("number"),
            "attachments": atts, "children": []}

    print(f"{'  ' * depth}└ [{depth}] {title}  "
          f"(첨부 {len(atts)})", flush=True)

    if max_depth is None or depth < max_depth:
        for ch in children_of(root_id):
            node["children"] += walk_tree(ch["id"], out_dir, seen, depth + 1,
                                          max_depth, stats)
    return [node]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", help="루트 페이지 ID")
    ap.add_argument("--space", help="스페이스 key — 최상위 페이지부터 전부")
    ap.add_argument("--search", help="제목 검색 후 매칭 페이지를 루트로")
    ap.add_argument("--list-spaces", action="store_true")
    ap.add_argument("--max-depth", type=int, default=None,
                    help="기본은 제한 없음")
    ap.add_argument("--out", default="./confluence_out")
    a = ap.parse_args()

    if a.list_spaces:
        for s in paged("/wiki/api/v2/spaces"):
            print(f"  {s['key']:20s} {s['id']:>10}  {s['name']}")
        return

    out = Path(a.out).resolve()
    out.mkdir(parents=True, exist_ok=True)
    stats = {"pages": 0, "attachments": 0, "bytes": 0}
    roots = []

    if a.root:
        roots = [a.root]
    elif a.search:
        cql = urllib.parse.quote(f'title ~ "{a.search}" and type = page')
        res = api(f"/wiki/rest/api/search?cql={cql}&limit=50")
        hits = [(r["content"]["id"], r["content"]["title"])
                for r in res.get("results", []) if r.get("content")]
        print(f"검색 '{a.search}' — {len(hits)}건")
        for i, t in hits:
            print(f"  {i}  {t}")
        roots = [i for i, _ in hits]
    elif a.space:
        sp = next((s for s in paged("/wiki/api/v2/spaces") if s["key"] == a.space), None)
        if not sp:
            sys.exit(f"스페이스 {a.space} 없음")
        roots = [p["id"] for p in paged("/wiki/api/v2/spaces/%s/pages" % sp["id"],
                                        {"depth": "root"})]
    else:
        sys.exit("--root / --space / --search 중 하나가 필요합니다.")

    tree, seen = [], set()
    for r in roots:
        tree += walk_tree(r, out, seen, 0, a.max_depth, stats)

    (out / "manifest.json").write_text(json.dumps({
        "captured_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "base_url": BASE,
        "scope": "full_depth_with_body_and_attachments",
        "max_depth": a.max_depth,
        "stats": stats,
        "tree": tree,
    }, ensure_ascii=False, indent=1), encoding="utf-8")

    print(f"\n페이지 {stats['pages']}개 · 첨부 {stats['attachments']}개 "
          f"({stats['bytes']/1048576:.1f}MB)\n→ {out}")


if __name__ == "__main__":
    main()
