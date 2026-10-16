#!/usr/bin/env python3
"""여러 페이지 대시보드를 **단일 HTML 한 장**으로 묶는다.

    python3 bundle.py

왜 필요한가 — 공유드라이브는 HTML을 렌더링하지 않는다. 받아서 브라우저로 열어야 하는데,
페이지가 9개에 이미지가 따로 있으면 한 파일만 받아서는 링크가 전부 깨진다.
그래서 **이미지를 base64 로 심고 페이지를 div 로 합쳐** 한 장으로 만든다.
받는 사람은 파일 하나만 열면 된다.
"""
from __future__ import annotations

import base64
import mimetypes
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "output"
BUNDLE = OUT / "정부과제_시스템_매뉴얼_통합.html"

PAGES = ["index", "accounts", "downloads", "people",
         "pms", "rcms", "enara", "kdca", "nipa", "iris", "kstartup", "g2b"]
EXCLUDED = {}   # 부서 내부 공유이므로 전 페이지를 넣는다


def inline_images(html: str) -> str:
    """<img src="img/x.png"> → data URI."""
    def sub(m):
        src = m.group(1)
        f = OUT / src
        if not f.exists():
            return m.group(0)
        mime = mimetypes.guess_type(f.name)[0] or "image/png"
        b64 = base64.b64encode(f.read_bytes()).decode()
        return f'src="data:{mime};base64,{b64}"'
    return re.sub(r'src="(img/[^"]+)"', sub, html)


def main() -> int:
    pages, css = {}, None
    for name in PAGES:
        f = OUT / f"{name}.html"
        if not f.exists():
            continue
        s = f.read_text(encoding="utf-8")
        if css is None:
            m = re.search(r"<style>(.*?)</style>", s, re.S)
            css = m.group(1) if m else ""
        m = re.search(r'<div class="app">(.*?)</div></div>\s*<script>', s, re.S)
        body = m.group(1) if m else re.search(r'<div class="app">(.*)', s, re.S).group(1)
        # 좌측 패널은 한 번만 쓰고 각 페이지에서는 뗀다
        body = re.sub(r'<aside class="side">.*?</aside>', "", body, flags=re.S)
        # `.doc` 여는 태그도 뗀다 — 닫는 태그가 구분자로 잘려 나가 페이지가 서로 중첩된다
        body = re.sub(r'<div class="doc"[^>]*>', "", body, count=1)
        pages[name] = inline_images(body)
        if name == "index":
            side = re.search(r'(<aside class="side">.*?</aside>)', s, re.S).group(1)

    # 좌측 패널 링크를 페이지 전환으로 바꾼다
    for x in EXCLUDED:
        side = re.sub(rf'<a class="sysl[^"]*" href="{x}\.html">.*?</a>', "", side, flags=re.S)
    side = re.sub(r'href="(\w+)\.html"', r'href="#" data-p="\1"', side)
    side = re.sub(r'class="sysl on"', 'class="sysl"', side)
    side = side.replace('data-p="index"', 'data-p="index" class-first', 1)
    side = side.replace('<a class="sysl" href="#" data-p="index" class-first',
                        '<a class="sysl on" href="#" data-p="index"')

    docs = "".join(f'<div class="page" data-p="{n}">{b}</div>' for n, b in pages.items())
    extra = """
.page{display:none}.page.on{display:block}
aside a.sysl{cursor:pointer}
.bundle-note{background:#fff7e6;border-bottom:1px solid #f0dcae;padding:9px 34px;
 font-size:12.5px;color:#8a6d1f}
"""
    html = f"""<!doctype html>
<html lang="ko"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>정부과제 시스템 매뉴얼 — 연구기획사업부</title>
<style>{css}{extra}</style></head><body><div class="app">
{side}
<div class="doc" style="display:flex;flex-direction:column">
<div class="bundle-note">한 파일 안에 모든 페이지가 들어 있습니다. 왼쪽 목록으로 이동하세요.
인터넷 연결 없이 열립니다.</div>
{docs}
</div></div>
<script>
function go(p){{
  document.querySelectorAll('.page').forEach(x=>x.classList.toggle('on',x.dataset.p===p));
  document.querySelectorAll('aside a.sysl').forEach(a=>a.classList.toggle('on',a.dataset.p===p));
  window.scrollTo({{top:0,behavior:'instant'}});
  bindTabs();
}}
function bindTabs(){{
  document.querySelectorAll('.page.on .sitemenu button').forEach(b=>b.onclick=()=>{{
    const sec=b.closest('.page');
    sec.querySelectorAll('.sitemenu button').forEach(x=>x.classList.remove('on'));
    b.classList.add('on');
    sec.querySelectorAll('.tabsec').forEach(s=>s.classList.toggle('on',s.dataset.t===b.dataset.t));
    window.scrollTo({{top:0,behavior:'instant'}});
  }});
}}
document.querySelectorAll('aside a.sysl').forEach(a=>a.onclick=e=>{{e.preventDefault();go(a.dataset.p);}});
document.querySelectorAll('.page .proj').forEach(a=>a.onclick=e=>{{e.preventDefault();go('index');}});
go('index');
</script>
</body></html>
"""
    BUNDLE.write_text(html, encoding="utf-8")
    kb = BUNDLE.stat().st_size / 1024
    print(f"· {BUNDLE.name}  {kb:,.0f}KB  (페이지 {len(pages)}개)")
    for k, why in EXCLUDED.items():
        print(f"  ✗ {k} 제외 — {why}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
