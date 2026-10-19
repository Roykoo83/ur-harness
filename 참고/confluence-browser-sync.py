#!/usr/bin/env python3
"""cmux 브라우저의 로그인 세션으로 Confluence를 수집한다.

API 토큰 없이, 이미 로그인된 브라우저 안에서 fetch를 실행해 데이터를 가져온다.
첨부는 base64로 받아 원본 그대로 저장하므로 기존 PoC가 막혔던 401을 피한다.

사용:
    confluence-browser-sync.py <surface> <out_dir> <root_id> [root_id ...]
"""
import base64
import json
import os
import subprocess
import sys
import time
from pathlib import Path

CHUNK_NOTE = "브라우저 eval 응답 크기 제한 때문에 페이지 단위로 나눠 받는다."


def ev(surface, js, timeout=180):
    """브라우저 안에서 JS를 실행하고 결과 문자열을 돌려준다."""
    r = subprocess.run(
        ["cmux", "browser", "--surface", surface, "eval", js],
        capture_output=True, timeout=timeout,
    )
    out = r.stdout.decode("utf-8", "replace").strip()
    if out.startswith("Error:"):
        raise RuntimeError(out[:300])
    return out


def jev(surface, js, timeout=180):
    raw = ev(surface, js, timeout)
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        raise RuntimeError(f"JSON 아님: {raw[:200]}")


def safe(name, limit=90):
    import re
    name = re.sub(r'[/\\:*?"<>|\n\r\t]', "_", str(name)).strip()
    return (name[:limit] or "untitled").rstrip(". ")


def storage_to_md(html):
    """Confluence storage HTML -> Markdown (표·리스트·참조 보존)."""
    import re
    from lxml import etree
    if not html:
        return ""
    wrapped = ('<div xmlns:ac="http://atlassian.com/content" '
               'xmlns:ri="http://atlassian.com/resource/identifier">' + html + "</div>")
    try:
        root = etree.fromstring(wrapped.encode(), etree.HTMLParser())
    except Exception:
        return re.sub(r"<[^>]+>", " ", html)

    out = []

    def txt(el):
        return " ".join("".join(el.itertext()).split())

    def localname(el):
        """HTMLParser는 네임스페이스를 풀지 않아 'ac:structured-macro' 같은 이름이
        그대로 들어온다. QName은 이런 이름에서 예외를 던지므로 직접 자른다."""
        t = el.tag
        if not isinstance(t, str):
            return ""
        if "}" in t:
            t = t.rsplit("}", 1)[-1]
        if ":" in t:
            t = t.rsplit(":", 1)[-1]
        return t.lower()

    def walk(el):
        tag = localname(el)
        if tag in ("h1", "h2", "h3", "h4", "h5"):
            t = txt(el)
            if t:
                out.append("#" * int(tag[1]) + " " + t)
            return
        if tag == "table":
            rows = []
            for tr in el.iter():
                if localname(tr) != "tr":
                    continue
                cells = [txt(td) for td in tr if localname(td) in ("td", "th")]
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
        if tag == "li":
            t = txt(el)
            if t:
                out.append("- " + t)
            return
        if tag == "p":
            t = txt(el)
            if t:
                out.append(t)
            return
        for ch in el:
            walk(ch)

    walk(root)

    refs = []
    for pat, label in ((r'ri:filename="([^"]+)"', "첨부"),
                       (r'ri:content-title="([^"]+)"', "페이지링크"),
                       (r'href="(https?://[^"]+)"', "외부링크")):
        for m in re.finditer(pat, html):
            refs.append(f"- [{label}] {m.group(1)}")
    if refs:
        out += ["", "## 참조"] + sorted(set(refs))
    return "\n\n".join(x for x in out if x.strip())


JS_ONE = """(async()=>{
const r=await fetch('/wiki/api/v2/pages/%s',{headers:{Accept:'application/json'}});
const p=await r.json();return JSON.stringify({id:'%s',title:p.title});})()"""

JS_KIDS = """(async()=>{
let o=[],u='/wiki/api/v2/pages/%s/children?limit=250';
while(u){const r=await fetch(u,{headers:{Accept:'application/json'}});const d=await r.json();
 o=o.concat((d.results||[]).map(x=>({id:x.id,title:x.title})));
 u=d._links&&d._links.next?d._links.next:null;}
return JSON.stringify(o);})()"""


def build_tree(surface, roots):
    """한 번에 한 노드씩 자식을 받아 전체 트리를 만든다.
    브라우저 eval은 장시간 실행에서 빈 응답을 돌려주므로 재귀를 파이썬 쪽에 둔다."""
    flat, seen = [], set()

    def walk(pid, title, depth, path):
        if pid in seen or depth > 12:
            return
        seen.add(pid)
        flat.append({"id": pid, "title": title, "depth": depth, "path": path})
        for k in jev(surface, JS_KIDS % pid, timeout=120):
            walk(k["id"], k["title"], depth + 1, path + " / " + k["title"])

    for r in roots:
        info = jev(surface, JS_ONE % (r, r))
        walk(r, info["title"], 0, info["title"])
    return flat

JS_PAGE = """(async()=>{
const r=await fetch('/wiki/api/v2/pages/%s?body-format=storage',{headers:{Accept:'application/json'}});
const p=await r.json();
const a=await fetch('/wiki/api/v2/pages/%s/attachments?limit=100',{headers:{Accept:'application/json'}});
const at=await a.json();
return JSON.stringify({title:p.title,version:(p.version||{}).number,
 when:(p.version||{}).createdAt,
 body:((p.body||{}).storage||{}).value||'',
 attachments:(at.results||[]).map(x=>({id:x.id,title:x.title,size:x.fileSize,
   media:x.mediaType,dl:(x._links||{}).download}))});})()"""

JS_ATT = """(async()=>{
const r=await fetch('%s');const b=await r.arrayBuffer();
const u=new Uint8Array(b);let s='';const C=0x8000;
for(let i=0;i<u.length;i+=C){s+=String.fromCharCode.apply(null,u.subarray(i,i+C));}
return btoa(s);})()"""


# 브라우저 eval 채널로 base64 전송하므로 대용량 첨부는 극단적으로 느리다.
# 상한을 넘는 파일은 목록에만 남기고 건너뛴 뒤, 필요할 때 개별로 받는다.
MAX_ATTACHMENT_BYTES = int(os.environ.get("CONF_MAX_ATT_MB", "8")) * 1024 * 1024


def main():
    if len(sys.argv) < 4:
        sys.exit(__doc__)
    surface, out_dir, roots = sys.argv[1], Path(sys.argv[2]), sys.argv[3:]
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "pages").mkdir(exist_ok=True)

    print("트리 수집 중...", flush=True)
    flat = build_tree(surface, roots)
    print(f"페이지 {len(flat)}개 발견\n", flush=True)

    stats = {"pages": 0, "attachments": 0, "bytes": 0, "errors": []}
    for i, node in enumerate(flat, 1):
        pid, title = node["id"], node["title"]
        try:
            p = jev(surface, JS_PAGE % (pid, pid))
        except Exception as e:                                   # noqa: BLE001
            stats["errors"].append({"page": pid, "title": title, "error": str(e)[:160]})
            print(f"  [{i}/{len(flat)}] ❌ {title[:44]}", flush=True)
            continue

        md = [f"# {title}", "",
              f"- page id: `{pid}`",
              f"- 계층: {node['path']}",
              f"- depth: {node['depth']}",
              f"- version: {p.get('version')} ({str(p.get('when') or '')[:10]})",
              "", "---", "", storage_to_md(p.get("body", ""))]
        (out_dir / "pages" / f"{node['depth']:02d}_{pid}_{safe(title)}.md").write_text(
            "\n".join(md), encoding="utf-8")
        stats["pages"] += 1
        node["attachments"] = []

        for at in p.get("attachments", []):
            dl = at.get("dl")
            if not dl:
                continue
            size = at.get("size") or 0
            if size > MAX_ATTACHMENT_BYTES:
                node["attachments"].append(
                    {"title": at["title"], "bytes": size, "skipped": "too_large",
                     "download": dl})
                stats.setdefault("skipped_large", []).append(
                    {"page": pid, "title": at["title"], "bytes": size})
                continue
            url = "/wiki" + dl if dl.startswith("/") else dl
            try:
                b64 = ev(surface, JS_ATT % url, timeout=300).strip('"')
                blob = base64.b64decode(b64)
                adir = out_dir / "attachments" / str(pid)
                adir.mkdir(parents=True, exist_ok=True)
                (adir / safe(at["title"])).write_bytes(blob)
                stats["attachments"] += 1
                stats["bytes"] += len(blob)
                node["attachments"].append(
                    {"title": at["title"], "bytes": len(blob),
                     "path": f"attachments/{pid}/{safe(at['title'])}"})
            except Exception as e:                               # noqa: BLE001
                stats["errors"].append(
                    {"page": pid, "attachment": at.get("title"), "error": str(e)[:160]})
        n_at = len(node["attachments"])
        print(f"  [{i}/{len(flat)}] {'  ' * min(node['depth'], 4)}{title[:44]}"
              f"{f'  📎{n_at}' if n_at else ''}", flush=True)
        time.sleep(0.15)

    (out_dir / "manifest.json").write_text(json.dumps({
        "captured_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "source": "{{WIKI_BASE}} (browser session)",
        "scope": "full_depth_with_body_and_attachments",
        "roots": roots, "stats": stats, "pages": flat,
    }, ensure_ascii=False, indent=1), encoding="utf-8")

    print(f"\n페이지 {stats['pages']} · 첨부 {stats['attachments']} "
          f"({stats['bytes']/1048576:.1f}MB) · 오류 {len(stats['errors'])}")
    print(f"→ {out_dir}")


if __name__ == "__main__":
    main()
