#!/usr/bin/env python3
"""정부과제 시스템 매뉴얼 — JSON → HTML.

    python3 build.py

**JSON이 정본, HTML은 생성물.** 매뉴얼을 판독해 JSON을 채우면 화면이 따라온다.
나중에 「시스템 입력용 산출물 생성 스킬」과 **브라우저 조작**이 같은 JSON을 읽는다.

화면 구조 (운영자 지시 2026-08-13)
    좌측 패널   시스템 목록 + 접속 링크 (정산 / 입찰 분리)
    상단 과제바  이 시스템을 쓰는 과제 — 클릭 가능, 진행 상태 표시
    사이트 메뉴  실제 사이트 메뉴 순서를 모사
    본문        워크플로우 · 매뉴얼 이미지 · 사례 · 혼선 정리 · 브라우저 조작
"""
from __future__ import annotations

import html
import json
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA, OUT = ROOT / "data", ROOT / "output"


def _find_drive() -> Path | None:
    """구글 드라이브 공유 드라이브 루트. 사람마다 계정·OS 가 달라 고정할 수 없다.

    2026-09-14: 계정 경로가 박혀 있어 이 맥에서만 돌았다. 부서원 PC(Windows)에서도
    돌아야 하므로 탐지로 바꾼다. 못 찾으면 None >> RCMS 원본 복사만 건너뛴다(비치명).
    """
    import os
    env = os.environ.get("UR_DRIVE")
    if env:
        return Path(env)
    home = Path.home()
    cloud = home / "Library" / "CloudStorage"          # macOS
    if cloud.is_dir():
        for base in sorted(cloud.glob("GoogleDrive-*")):
            for n in ("공유 드라이브", "Shared drives"):
                if (base / n).is_dir():
                    return base / n
    if os.name == "nt":                                 # Windows
        for letter in "GHIJKLMNOPQRSTUVWXYZDEF":
            for n in ("공유 드라이브", "Shared drives"):
                p = Path(f"{letter}:/") / n
                try:
                    if p.is_dir():
                        return p
                except OSError:
                    continue
        for n in ("공유 드라이브", "Shared drives"):
            p = home / "Google Drive" / n
            if p.is_dir():
                return p
    return None


DRIVE = _find_drive()
RCMS_SRC = (DRIVE / "U/2. R&D 사업/2-3. 수행중인 사업"
            / "1. 팁스 글로벌트랙 (2025.09-2028.12)/RCMS 매뉴얼/RCMS_사용법_대시보드.html"
            ) if DRIVE else None

CSS = """
:root{--blue:#0b6fc4;--blue-d:#0a4d8c;--blue-l:#e8f2fb;--navy:#123c66;
 --green:#0f9d76;--green-l:#e6f6f0;--orange:#e07b00;--orange-l:#fdf1e0;
 --red:#d64545;--red-l:#fceaea;--purple:#7c5cd6;--purple-l:#f0ecfb;
 --ink:#1c2733;--sub:#5b6b7c;--line:#dfe7ee;--bg:#f4f7fa;--card:#fff;--side:#0f2537}
*{box-sizing:border-box;margin:0;padding:0}
body{font-family:'Pretendard','Apple SD Gothic Neo','Malgun Gothic',sans-serif;
 background:var(--bg);color:var(--ink);line-height:1.6}
.app{display:flex;min-height:100vh}

aside.side{width:232px;flex:none;background:var(--side);color:#cfe0ef;
 position:sticky;top:0;height:100vh;overflow-y:auto;padding:18px 0 40px}
aside .brand{padding:0 18px 16px;border-bottom:1px solid rgba(255,255,255,.12);margin-bottom:12px}
aside .brand b{display:block;color:#fff;font-size:15px;letter-spacing:-.01em}
aside .brand span{font-size:11.5px;opacity:.6}
aside .grp{padding:14px 18px 6px;font-size:11px;font-weight:700;letter-spacing:.09em;
 color:#7fa8cc;text-transform:uppercase}
aside a.sysl{display:flex;align-items:center;gap:6px;padding:9px 18px;color:#cfe0ef;
 text-decoration:none;font-size:13.5px;border-left:3px solid transparent}
aside a.sysl:hover{background:rgba(255,255,255,.06);color:#fff}
aside a.sysl.on{background:rgba(11,111,196,.28);border-left-color:#4da3e8;color:#fff;font-weight:700}
aside a.sysl small{display:block;font-size:10.5px;opacity:.55}
aside .link{display:block;padding:5px 18px 7px 30px;font-size:11px;color:#8fb6d6;
 text-decoration:none;word-break:break-all}
aside .link:hover{color:#fff;text-decoration:underline}

.doc{flex:1;min-width:0}
header{background:linear-gradient(120deg,var(--blue-d),var(--blue));color:#fff;padding:22px 34px}
header h1{font-size:22px;margin-bottom:4px}
header p{opacity:.9;font-size:13px}
header .src{margin-top:8px;font-size:11.5px;opacity:.72}

.projbar{background:#fff;border-bottom:1px solid var(--line);padding:12px 34px;
 display:flex;gap:8px;flex-wrap:wrap;align-items:center}
.projbar .lb{font-size:11.5px;font-weight:700;color:var(--sub);letter-spacing:.06em;margin-right:4px}
.proj{display:inline-flex;flex-direction:column;text-decoration:none;color:inherit;
 border:1px solid var(--line);border-radius:9px;padding:7px 12px;background:#fafcfe;transition:.15s}
.proj:hover{border-color:var(--blue);box-shadow:0 3px 10px rgba(16,42,67,.1)}
.proj b{font-size:13px}
.proj em{font-style:normal;font-size:11px;margin-top:2px}
.proj.run em{color:var(--green)}.proj.warn em{color:var(--orange)}
.proj.end em{color:var(--sub)}.proj.idle em{color:var(--sub)}
.proj.run{border-left:3px solid var(--green)}.proj.warn{border-left:3px solid var(--orange)}
.proj.end{border-left:3px solid #b9c6d2}.proj.idle{border-left:3px solid #d8e0e8}

.sitemenu{background:var(--navy);color:#fff;padding:0 34px;display:flex;overflow-x:auto;
 position:sticky;top:0;z-index:40}
.sitemenu button{flex:none;padding:11px 15px;font-size:13.5px;font-weight:600;white-space:nowrap;
 background:none;border:none;color:#fff;opacity:.6;cursor:pointer;border-bottom:3px solid transparent;
 font-family:inherit}
.sitemenu button:hover{opacity:.9}
.sitemenu button.on{opacity:1;border-bottom-color:#4da3e8}
.sitemenu button.empty{opacity:.32}
.sitemenu button .n{display:inline-block;background:#4da3e8;color:#08243c;font-size:10px;font-weight:800;
 border-radius:99px;padding:1px 6px;margin-left:5px;vertical-align:1px}
.sitemenu .tagline{margin-left:auto;font-size:11px;opacity:.5;padding:11px 0;white-space:nowrap}
.tabsec{display:none}.tabsec.on{display:block}
.lead{font-size:14px;color:var(--sub);margin:2px 0 16px}
.tlwrap{overflow-x:auto;border:1px solid var(--line);border-radius:10px;background:#fff}
table.tl{border-collapse:separate;border-spacing:0;font-size:10.5px;table-layout:fixed}
table.tl th,table.tl td{border:0;border-bottom:1px solid #eef2f6;padding:3px 2px;
 white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
table.tl thead th{position:sticky;top:0;background:#eef3f8;color:var(--navy);font-size:10px;
 font-weight:700;text-align:center;z-index:2}
table.tl thead th.yr{background:var(--navy);color:#fff;font-size:11px;letter-spacing:.03em}
table.tl th.nm,table.tl td.nm{position:sticky;left:0;background:#fff;z-index:3;text-align:left;
 font-weight:700;width:96px;padding-left:10px;border-right:1px solid var(--line)}
table.tl th.pj,table.tl td.pj{position:sticky;left:96px;background:#fff;z-index:3;text-align:left;
 color:var(--sub);width:186px;padding-left:8px;border-right:1px solid var(--line)}
table.tl thead th.nm,table.tl thead th.pj{background:#eef3f8;z-index:4}
table.tl col.m{width:34px}
table.tl td.v{text-align:center;font-variant-numeric:tabular-nums}
table.tl tr.sum td{background:#f6f9fc;font-weight:800;border-top:1px solid var(--line)}
table.tl tr.sum td.nm,table.tl tr.sum td.pj{background:#f6f9fc}
td.v.r0{color:#c9d3dc}
td.v.r1{background:#eef7f2}td.v.r2{background:#d8efe4}td.v.r3{background:#bde5d3}
td.v.over{background:#fdeaea;color:var(--red);font-weight:800}
.lgd{display:flex;gap:14px;font-size:11.5px;color:var(--sub);margin:8px 0 0;flex-wrap:wrap}
.lgd i{display:inline-block;width:13px;height:13px;border-radius:3px;vertical-align:-2px;margin-right:4px}
.src-b{display:inline-block;font-size:10px;font-weight:700;padding:1px 6px;border-radius:99px;margin-left:5px}
.src-sys{background:var(--green-l);color:var(--green)}
.src-sheet{background:#eef3f8;color:var(--sub)}
.src-wip{background:var(--orange-l);color:var(--orange)}
.empty-note{color:var(--sub);font-size:13.5px;padding:18px 20px;background:#fff;
 border:1px dashed var(--line);border-radius:12px}

main{max-width:1720px;padding:24px 34px 80px}
.narrow{max-width:1000px}
h2{font-size:19px;margin:26px 0 12px;padding-left:10px;border-left:4px solid var(--blue)}
h3{font-size:15.5px;margin:18px 0 8px;color:var(--navy)}
p,li,td,th{font-size:13.8px}
.card{background:var(--card);border:1px solid var(--line);border-radius:12px;
 padding:17px 20px;margin-bottom:14px;box-shadow:0 1px 3px rgba(16,42,67,.05)}
.grid{display:grid;gap:14px}.g2{grid-template-columns:repeat(auto-fill,minmax(300px,1fr))}
.badge{display:inline-block;font-size:11.5px;font-weight:700;padding:3px 9px;border-radius:99px;margin-right:5px}
.b-ok{background:var(--green-l);color:var(--green)}.b-run{background:var(--blue-l);color:var(--blue)}
.b-none{background:var(--red-l);color:var(--red)}.b-todo{background:var(--orange-l);color:var(--orange)}
.tag{display:inline-block;font-size:11.5px;background:#eef3f8;color:var(--sub);padding:3px 8px;
 border-radius:5px;margin:0 4px 4px 0}
table{width:100%;border-collapse:collapse;margin:8px 0}
th,td{border:1px solid var(--line);padding:8px 11px;text-align:left;vertical-align:top}
th{background:#eef3f8;font-weight:700;color:var(--navy);white-space:nowrap}
.flow{display:grid;gap:9px;grid-template-columns:repeat(auto-fill,minmax(185px,1fr))}
.stage{background:#fff;border:1px solid var(--line);border-radius:10px;overflow:hidden}
.stage .t{background:var(--blue-l);color:var(--blue-d);font-weight:700;font-size:13.5px;
 padding:8px 11px;border-bottom:1px solid var(--line)}
.stage ul{margin:0;padding:9px 11px 11px 24px}.stage li{font-size:12.5px;color:var(--sub);margin-bottom:3px}
.proc{border-left:4px solid var(--blue);padding-left:14px;margin:16px 0}
.proc .code{font:700 11px/1 ui-monospace,monospace;color:var(--sub);letter-spacing:.04em}
.path{background:#0f2537;color:#cfe4f5;font:600 12.5px/1.5 ui-monospace,monospace;
 padding:8px 12px;border-radius:7px;margin:8px 0;word-break:break-all}
.warn{background:var(--orange-l);border-left:4px solid var(--orange);padding:10px 14px;
 border-radius:0 8px 8px 0;margin:10px 0;font-size:13.4px}.warn b{color:var(--orange)}
.miss{background:var(--red-l);border-left:4px solid var(--red);padding:10px 14px;
 border-radius:0 8px 8px 0;margin:10px 0;font-size:13.4px}
.ex{background:var(--green-l);border-left:4px solid var(--green);padding:10px 14px;
 border-radius:0 8px 8px 0;margin:10px 0;font-size:13.4px}.ex b{color:var(--green)}
.bot{background:var(--purple-l);border-left:4px solid var(--purple);padding:10px 14px;
 border-radius:0 8px 8px 0;margin:10px 0;font-size:13px}
.bot b{color:var(--purple)}
.bot ol{margin:6px 0 0 20px}.bot li{font-size:12.8px;margin-bottom:3px}
.bot code{background:#fff;padding:1px 5px;border-radius:4px;font-size:12px}
figure{margin:12px 0;border:1px solid var(--line);border-radius:10px;overflow:hidden;background:#fff}
figure img{width:100%;display:block}
figcaption{font-size:12.3px;color:var(--sub);padding:8px 12px;background:#fafcfe;border-top:1px solid var(--line)}
ol.steps{margin:8px 0 8px 20px}ol.steps li{margin-bottom:5px}
.note{font-size:12.3px;color:var(--sub);border:1px dashed var(--line);border-radius:8px;
 padding:10px 13px;margin-top:12px;background:#fafcfe}
"""

BADGE = {"완료": "b-ok", "정리 진행": "b-run", "관문": "b-ok",
         "미착수": "b-todo", "매뉴얼 미확보": "b-none"}


def e(s) -> str:
    return html.escape(str(s or ""))


def md(s: str) -> str:
    out, parts = "", e(s).split("**")
    for i, p in enumerate(parts):
        out += f"<b>{p}</b>" if i % 2 else p
    return out


def badge_class(st: str) -> str:
    for k, v in BADGE.items():
        if st.startswith(k):
            return v
    return "b-todo"


def side(cfg: dict, cur: str) -> str:
    s = ('<aside class="side"><div class="brand"><b>정부과제 시스템</b>'
         '<span>연구기획사업부 매뉴얼</span></div>')
    s += '<a class="sysl%s" href="index.html">전체 색인</a>' % ("" if cur else " on")
    s += ('<a class="sysl%s" href="accounts.html">계좌·카드 매핑'
          '<small>과제별 통장·사업비카드</small></a>' % (" on" if cur == "accounts" else ""))
    s += ('<a class="sysl%s" href="downloads.html">시스템 다운로드'
          '<small>무엇을 어디서 뽑나</small></a>' % (" on" if cur == "downloads" else ""))
    s += ('<a class="sysl%s" href="people.html">참여인력·인건비'
          '<small>참여율 · 월 집행계획</small></a>' % (" on" if cur == "people" else ""))
    for cat, label in (("정산", "정산 시스템 · 상시"), ("입찰", "입찰·접수 · 이벤트성")):
        s += f'<div class="grp">{label}</div>'
        for y in sorted([x for x in cfg["systems"] if x["category"] == cat],
                        key=lambda x: x["우선순위"]):
            on = " on" if y["code"] == cur else ""
            s += (f'<a class="sysl{on}" href="{y["code"]}.html">'
                  f'<span>{e(y["name"])}<small>{e(y["full"][:22])}</small></span></a>')
            u = (y.get("url") or "").split(" ")[0]
            if u.startswith("http"):
                s += f'<a class="link" href="{e(u)}" target="_blank">{e(u)} ↗</a>'
    return s + "</aside>"


def projbar(cfg: dict, projects: list) -> str:
    st = cfg["과제상태"]
    if not projects:
        return ""
    s = '<div class="projbar"><span class="lb">관련 과제</span>'
    for p in projects:
        m = st.get(p, {"단계": "정보 없음", "기간": "-", "색": "idle"})
        s += (f'<a class="proj {m["색"]}" href="index.html#과제" title="{e(m["기간"])}">'
              f'<b>{e(p)}</b><em>{e(m["단계"])}</em></a>')
    return s + "</div>"


def sitemenu(y: dict, counts: dict) -> str:
    mm = y.get("site_menu") or []
    split = y.get("menu_split") or {}
    bid = set(split.get("입찰", []))
    s = '<div class="sitemenu"><button class="on" data-t="개요">개요</button>'
    for m in mm:
        n = counts.get(m, 0)
        cls = "empty" if not n else ""
        badge = f'<span class="n">{n}</span>' if n else ""
        s += f'<button class="{cls}" data-t="{e(m)}">{e(m)}{badge}</button>'
    if split:
        s += ('<span class="tagline">'
              + " · ".join(e(x) for x in bid) + ' 는 입찰 영역</span>')
    return s + "</div>"


def page(cfg, y, body: str, menu_html: str = "") -> str:
    title = f'{y["name"]} — {y["full"]}' if y else "정부과제 시스템 매뉴얼"
    sub = y["역할"] if y else "사업비 집행·정산 시스템과 입찰·접수 시스템 총정리"

    return f"""<!doctype html>
<html lang="ko"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{e(title)}</title><style>{CSS}</style></head><body><div class="app">
{side(cfg, y["code"] if y else "")}
<div class="doc">
<header><h1>{e(title)}</h1><p>{e(sub)}</p></header>
{projbar(cfg, y["과제"]) if y else ""}
{menu_html}
<main>{body}</main>
</div></div>
<script>
document.querySelectorAll('.sitemenu button').forEach(b=>b.onclick=()=>{{
  document.querySelectorAll('.sitemenu button').forEach(x=>x.classList.remove('on'));
  b.classList.add('on');
  const t=b.dataset.t;
  document.querySelectorAll('.tabsec').forEach(s=>s.classList.toggle('on',s.dataset.t===t));
  window.scrollTo({{top:0,behavior:'instant'}});
}});
</script>
</body></html>
"""


def build_index(cfg) -> str:
    ss = cfg["systems"]
    body = ""
    for cat, title, desc in (
            ("정산", "정산 시스템 — 메인",
             "사업비를 실제로 집행·정산하는 시스템. 상시 사용하며 예산 업무의 중심이다."),
            ("입찰", "입찰·접수 시스템 — 서브",
             "공고 조회·신청·접수. 연 20~30회 이벤트성이지만, 협약변경처럼 정산과 이어지는 지점이 있다.")):
        rows = sorted([x for x in ss if x["category"] == cat], key=lambda x: x["우선순위"])
        cards = ""
        for y in rows:
            tags = "".join(f'<span class="tag">{e(p)}</span>' for p in y["과제"])
            u = (y.get("url") or "").split(" ")[0]
            link = (f'<div style="margin-top:8px;font-size:12px"><a href="{e(u)}" target="_blank" '
                    f'style="color:var(--blue)">{e(y["url"])} ↗</a></div>') if u.startswith("http") else ""
            cards += f"""<div class="card">
  <h3 style="margin-top:0"><a href="{y['code']}.html" style="color:var(--blue-d);text-decoration:none">{e(y['name'])}</a></h3>
  <div style="font-size:12.5px;color:var(--sub)">{e(y['full'])} · {e(y['운영'])}</div>
  <p style="margin:9px 0">{md(y['역할'])}</p>
  <span class="badge {badge_class(y['상태'])}">{e(y['상태'])}</span>
  <div style="margin-top:8px">{tags}</div>{link}
</div>"""
        body += f'<h2>{title}</h2><p>{desc}</p><div class="grid g2" style="margin-top:14px">{cards}</div>'

    proj: dict = {}
    for y in ss:
        for p in y["과제"]:
            proj.setdefault(p, []).append(f'{y["name"]}·{y["category"]}')
    st = cfg["과제상태"]
    rows = ""
    for p, v in sorted(proj.items()):
        m = st.get(p, {"단계": "-", "색": "idle"})
        cls = {"run": "b-ok", "warn": "b-todo", "end": "b-none"}.get(m["색"], "b-run")
        rows += (f'<tr><td>{e(p)}</td><td><span class="badge {cls}">{e(m["단계"])}</span></td>'
                 f'<td>{"".join(f"<span class=tag>{e(n)}</span>" for n in v)}</td></tr>')
    body += (f'<h2 id="과제">과제 × 시스템</h2><table><thead><tr><th style="width:32%">과제</th>'
             f'<th style="width:24%">상태</th><th>사용 시스템</th></tr></thead><tbody>{rows}</tbody></table>')
    return page(cfg, None, body)


def render_proc(p: dict) -> str:
    s = f'<div class="card proc"><div class="code">{e(p["code"])} · {e(p["phase"])}</div>'
    s += f'<h3>{e(p["title"])}'
    if p.get("상태"):
        s += f' <span class="badge b-todo">{e(p["상태"])}</span>'
    s += "</h3>"
    if p.get("menu_path"):
        s += f'<div class="path">📍 {e(p["menu_path"])}</div>'
    if p.get("overview"):
        s += f"<p>{md(p['overview'])}</p>"
    for img in p.get("images", []):
        s += (f'<figure><img src="img/{e(img["file"])}" alt="{e(img.get("caption"))}">'
              f'<figcaption>{md(img.get("caption",""))}</figcaption></figure>')
    if p.get("process"):
        pr = p["process"]
        s += f'<h3>워크플로우</h3><div class="path">진입 · {e(pr["진입"])}</div><div class="flow">'
        br = pr["분기"]
        s += ('<div class="stage"><div class="t">사용내역조회</div><ul>'
              + "".join(f"<li>{e(x)}</li>" for x in br["사용내역조회"]) + "</ul></div>")
        for way, seq in br["사용내역등록"]["경로"].items():
            s += (f'<div class="stage"><div class="t">등록 · {e(way)}</div><ul>'
                  + "".join(f"<li>{e(x)}</li>" for x in seq) + "</ul></div>")
        s += "</div>"
        ap = pr["승인처리"]["승인상태"]
        s += ("<h3>승인 처리</h3><table><thead><tr><th>승인상태</th><th>다음 단계</th></tr></thead><tbody>"
              + "".join(f"<tr><td>{e(k)}</td><td>{e(v)}</td></tr>" for k, v in ap.items())
              + "</tbody></table>")
    if p.get("functions"):
        s += "<h3>주요 기능</h3><ul style='margin-left:20px'>" + \
             "".join(f"<li>{md(x)}</li>" for x in p["functions"]) + "</ul>"
    if p.get("search_filters"):
        s += "<h3>조회 조건</h3><table>" + "".join(
            f"<tr><th>{e(k)}</th><td>{e(', '.join(v) if isinstance(v, list) else v)}</td></tr>"
            for k, v in p["search_filters"].items()) + "</table>"
    if p.get("steps"):
        s += "<h3>사용 절차</h3><ol class='steps'>" + \
             "".join(f"<li>{md(x['action'])}</li>" for x in p["steps"]) + "</ol>"
    if p.get("상세조회"):
        d = p["상세조회"]
        s += ("<h3>상세조회</h3><table>"
              f"<tr><th style='width:140px'>메뉴</th><td>{e(d['메뉴'])}</td></tr>"
              f"<tr><th>기본정보</th><td>{e(', '.join(d['기본정보']))}</td></tr>"
              + "".join(f"<tr><th>{e(k)}</th><td>{e(v)}</td></tr>" for k, v in d["버튼"].items())
              + "".join(f"<tr><th>상태 · {e(k)}</th><td>{e(', '.join(v))}</td></tr>"
                        for k, v in d["상태별_가능동작"].items()) + "</table>")
    if p.get("example"):
        s += f'<div class="ex"><b>사례</b> — {md(p["example"])}</div>'
    if p.get("혼선"):
        s += ('<div class="warn"><b>헷갈리는 것 정리</b><ul style="margin:6px 0 0 18px">'
              + "".join(f"<li>{md(x)}</li>" for x in p["혼선"]) + "</ul></div>")
    if p.get("pitfalls"):
        s += ('<div class="warn"><b>주의사항</b><ul style="margin:6px 0 0 18px">'
              + "".join(f"<li>{md(x)}</li>" for x in p["pitfalls"]) + "</ul></div>")
    if p.get("browser_ops"):
        s += ('<div class="bot"><b>브라우저 조작 절차</b> — 에이전트가 이 순서로 수행한다<ol>'
              + "".join(f"<li>{md(x)}</li>" for x in p["browser_ops"]) + "</ol></div>")
    return s + "</div>"


def build_accounts(cfg) -> str:
    a = json.loads((DATA / "accounts.json").read_text(encoding="utf-8"))
    banks = "".join(
        f'<tr><td><b>{e(b["이름"])}</b></td><td>{e(b["용도"])}</td>'
        f'<td><a href="{e(b["url"])}" target="_blank" style="color:var(--blue)">{e(b["url"])} ↗</a></td>'
        f'<td>{e(b["비고"])}</td></tr>' for b in a["금융기관"])

    rows = ""
    for x in a["과제"]:
        cards = "<br>".join(
            f'{e(c["사"])} <b>****-{e(c["뒤4"])}</b> <span style="color:var(--sub)">{e(c["소유"])}</span>'
            for c in x["카드"]) or '<span style="color:var(--sub)">—</span>'
        pay = f'{x["월편성_인건비"]:,}' if x.get("월편성_인건비") else "—"
        st = x["상태"]
        cls = "b-ok" if st == "정상" else ("b-none" if "불가" in st or "미정" in st else "b-todo")
        rows += (f'<tr><td><b>{e(x["과제"])}</b></td><td><span class="tag">{e(x["시스템"])}</span></td>'
                 f'<td>{e(x["은행"])}<br><span style="color:var(--sub);font-size:12.5px">{e(x["계좌_뒤"])}</span></td>'
                 f'<td>{cards}</td><td style="text-align:right">{pay}</td>'
                 f'<td><span class="badge {cls}">{e(st)}</span></td></tr>')

    memo = "".join(f"<li>{md(m)}</li>" for m in a["메모"])

    sv = ""
    for c in (a.get("용역계약") or {}).get("계약", []):
        tiers = "".join(
            f'<tr><td><b>{e(t["연차"])}</b></td>'
            f'<td style="text-align:right">{t["계약금액"]:,}</td>'
            f'<td style="text-align:right">{t["선금"]:,}<br>'
            f'<span style="color:var(--sub);font-size:12px">{t["선금률"]*100:.0f}%</span></td>'
            f'<td style="text-align:right">{t["잔금"]:,}</td>'
            f'<td>{e(t["상태"])}</td><td>{e(t.get("비고",""))}</td></tr>' for t in c["차수"])
        pay = "".join(f"<li>{md(x)}</li>" for x in c["지급조건"])
        cond = "".join(f"<tr><th>{e(k)}</th><td>{e(v)}</td></tr>" for k, v in c["조건"].items())
        sv += (f'<div class="card"><h3 style="margin-top:0">{e(c["과제"])} '
               f'<span class="badge b-run">{e(c["성격"])}</span></h3>'
               f'<p style="font-size:13px;color:var(--sub)">{e(c["원사업자"])} → {e(c["수급사업자"])} · '
               f'과업 {e(c["과업"])}</p>'
               f'<table><thead><tr><th>연차</th><th style="text-align:right">계약금액(VAT 포함)</th>'
               f'<th style="text-align:right">선금</th><th style="text-align:right">잔금</th>'
               f'<th>상태</th><th>비고</th></tr></thead><tbody>{tiers}</tbody></table>'
               f'<h3>기성 지급 조건</h3><ul style="margin-left:20px">{pay}</ul>'
               f'<h3>계약 조건</h3><table>{cond}</table>'
               f'<div class="note">근거: {e(c["근거"])}</div></div>')
    if sv:
        sv = (f'<h2>용역 계약 — 기성</h2>'
              f'<div class="ex"><b>R&amp;D 과제와 다르다</b> — {md(a["용역계약"]["_설명"])}</div>{sv}')
    body = f"""<div class="tabsec on" data-t="개요">
<h2>과제별 통장 · 사업비카드</h2>
<div class="card"><table>
<thead><tr><th style="width:23%">과제</th><th>시스템</th><th>통장</th><th>사업비카드</th>
<th style="text-align:right">월편성 인건비</th><th>상태</th></tr></thead>
<tbody>{rows}</tbody></table>
<div class="warn"><b>번호를 뒷자리만 싣는 이유</b> 이 저장소는 GitHub 로 푸시된다.
카드번호·계좌번호 전체는 올리지 않고 <b>식별에 필요한 뒷자리만</b> 둔다.
전체 번호는 원본 시트에 있다 — <code>{e(a["_출처"].split(" · ")[0])}</code></div>
</div>

{sv}
<h2>금융기관 사이트</h2>
<div class="card"><table>
<thead><tr><th style="width:16%">기관</th><th style="width:16%">용도</th><th>주소</th><th>비고</th></tr></thead>
<tbody>{banks}</tbody></table></div>

<h2>알아둘 것</h2>
<div class="card"><ul style="margin-left:20px">{memo}</ul></div>
</div>"""
    fake = {"code": "accounts", "name": "계좌·카드 매핑", "full": "과제별 통장·사업비카드",
            "역할": f"과제 8건 · 원본 시트 {a['갱신']} 기준", "과제": [], "site_menu": []}
    return page(cfg, fake, body, '<div class="sitemenu"><button class="on" data-t="개요">전체</button></div>')


def _months(a, b):
    y, m = int(a[:4]), int(a[5:7]); Y, M = int(b[:4]), int(b[5:7])
    while (y, m) <= (Y, M):
        yield f"{y:04d}-{m:02d}"
        m += 1
        if m > 12:
            y, m = y + 1, 1


def _timeline(d) -> str:
    """과제 × 인력 × 월 참여율. 월 합계 100% 초과를 잡는 것이 목적이다."""
    tl, span = d["타임라인"], d["과제기간"]
    ms = list(_months(tl["시작"], tl["종료"]))

    # 연도 헤더
    yrs, cnt = [], {}
    for m in ms:
        cnt[m[:4]] = cnt.get(m[:4], 0) + 1
    for y in sorted(cnt):
        yrs.append(f'<th class="yr" colspan="{cnt[y]}">{y}</th>')
    cols = ('<colgroup><col style="width:96px"><col style="width:186px">'
            + '<col class="m">' * len(ms) + "</colgroup>")
    head = ('<tr><th class="nm"></th><th class="pj"></th>' + "".join(yrs) + "</tr>"
            '<tr><th class="nm">이름</th><th class="pj">과제</th>'
            + "".join(f"<th>{m[5:7]}</th>" for m in ms) + "</tr>")

    def active(pr, m, person):
        if pr not in span:
            return False
        a, b = span[pr]
        if not (a <= m <= b):
            return False
        q = person.get("퇴사")
        if q and len(q) >= 7 and q[:4].isdigit():
            end = q.replace(".", "-")[:7]
            if m > end:
                return False
        return True

    rows = ""
    for x in sorted(d["인력"], key=lambda z: -z["총참여율"]):
        mine = list(x["현금참여율"].items()) + [(k, v) for k, v in x["현물참여율"].items()]
        if not mine and not x.get("메모") and not str(x.get("출처","")).startswith("변경"):
            x["메모"] = "시스템에 참여율이 등록돼 있지 않다."
        block = ""
        totals = {m: 0 for m in ms}
        if not mine:
            block = (f'<tr><td class="nm"></td><td class="pj" colspan="{len(ms)+1}" '
                     f'style="color:var(--orange)">{e(x.get("메모",""))}</td></tr>')
        if not mine and not str(x.get("출처", "")).startswith("변경"):
            block = (f'<tr><td class="nm"></td><td class="pj" colspan="{len(ms)+1}" '
                     f'style="color:var(--orange)">{e(x.get("메모",""))}</td></tr>')
        for pr, rate in mine:
            cells = ""
            for m in ms:
                on = active(pr, m, x)
                v = rate if on else 0
                totals[m] = round(totals[m] + v, 1)
                cls = "r0" if not v else ("r1" if v < 30 else "r2" if v < 60 else "r3")
                cells += (f'<td class="v {cls}">{v:g}</td>' if v
                          else '<td class="v r0">·</td>')
            kind = " (현물)" if pr in x["현물참여율"] else ""
            block += (f'<tr><td class="nm"></td><td class="pj">{e(pr)}{kind}</td>{cells}</tr>')
        sums = "".join(
            f'<td class="v {"over" if totals[m] > 100 else ""}">{round(totals[m]):d}</td>'
            if totals[m] else '<td class="v r0">·</td>'
            for m in ms)
        src = str(x.get("출처", ""))
        sb = ("src-sys" if src.startswith("시스템") else
              "src-wip" if src.startswith("변경") else "src-sheet")
        lbl = "시스템" if sb == "src-sys" else ("변경중" if sb == "src-wip" else "시트")
        left = (f'<tr class="sum"><td class="nm">{e(x["이름"])}'
                f'<span class="src-b {sb}">{lbl}</span>'
                + ('<br><span style="font-weight:400;color:var(--red);font-size:10px">퇴사 '
                   + e(x["퇴사"]) + "</span>" if x["퇴사"] else "")
                + f'</td><td class="pj">합계</td>{sums}</tr>')
        rows += left + block

    over_m = set()
    for x in d["인력"]:
        for m in ms:
            t = 0
            for pr, rate in list(x["현금참여율"].items()) + list(x["현물참여율"].items()):
                if active(pr, m, x):
                    t += rate
            if t > 100:
                over_m.add((x["이름"], m))
    names = sorted({n for n, _ in over_m})
    warn = (f'<div class="warn"><b>월 합계가 100%를 넘는 달이 있는 인원 {len(names)}명</b> — '
            + ", ".join(e(n) for n in names)
            + '. 연구개발계획서 부록의 「정부과제 총 인건비계상률」은 <b>전원 100%</b>다. '
              '한 사람의 월 참여율 합계는 100%를 넘을 수 없다.</div>') if names else ""

    pol = f'<div class="ex"><b>정본은 시스템이다</b> — {md(d["_정본"])}</div>'
    wip = [x for x in d["인력"] if str(x.get("출처", "")).startswith("변경")]
    if wip:
        h = (d.get("변경이력") or [{}])[0]
        pol += ('<div class="warn"><b>참여율 변경 진행 중 '
                + f'{len(wip)}명</b> — ' + ", ".join(e(x["이름"]) for x in wip)
                + f'. {e(h.get("과제",""))} · {e(h.get("상태",""))}<br>'
                + f'<span style="font-size:12.5px">{e(h.get("근거",""))}</span><br>'
                + f'<span style="font-size:12.5px">다음 — {e(h.get("다음",""))}</span></div>')
    return (pol + warn
            + f'<div class="tlwrap"><table class="tl" style="width:{282+34*len(ms)}px">'
            + cols + f'<thead>{head}</thead>'
            f'<tbody>{rows}</tbody></table></div>'
            '<div class="lgd"><span><i style="background:#eef7f2"></i>~29%</span>'
            '<span><i style="background:#d8efe4"></i>30~59%</span>'
            '<span><i style="background:#bde5d3"></i>60%~</span>'
            '<span><i style="background:#fdeaea"></i>합계 100% 초과</span>'
            '<span>· = 과제 기간 밖 또는 퇴사 이후</span></div>'
            f'<div class="note">{md(d["타임라인"]["_주의"])}</div>')


def build_downloads(cfg) -> str:
    d = json.loads((DATA / "downloads.json").read_text(encoding="utf-8"))
    need = "".join(
        f'<tr><td><b>{e(x["코드"])}</b></td><td><b>{e(x["이름"])}</b></td><td>{e(x["내용"])}</td>'
        f'<td>{md(x["용도"])}</td><td>{e(x["주기"])}</td></tr>' for x in d["필요데이터"])

    sysblocks = ""
    for y in d["시스템"]:
        rows = "".join(
            f'<tr><td><b>{e(i["코드"])}</b></td><td>{e(i["경로"])}</td>'
            f'<td>{md(i.get("확인",""))}</td>'
            f'<td>{e(i.get("파일","")) if i.get("파일") else ""}</td></tr>' for i in y["항목"])
        u = y["url"]
        sysblocks += (f'<div class="card"><h3 style="margin-top:0">{e(y["name"])} '
                      f'<span class="badge {"b-ok" if y["상태"].startswith("✅") else "b-run" if y["상태"].startswith("◑") else "b-todo"}">'
                      f'{e(y["상태"])}</span></h3>'
                      f'<p style="font-size:12.5px;color:var(--sub)">'
                      f'<a href="{e(u)}" target="_blank" style="color:var(--blue)">{e(u)} ↗</a> · '
                      f'{" · ".join(e(p) for p in y["과제"])}</p>'
                      f'<table><thead><tr><th style="width:44px">항목</th><th style="width:30%">메뉴 경로</th>'
                      f'<th>확인 내용</th><th style="width:20%">확보 파일</th></tr></thead>'
                      f'<tbody>{rows}</tbody></table></div>')

    m = d["복원방법"]
    steps = "".join(f"<li>{md(x)}</li>" for x in m["단계"])
    cautions = "".join(f"<li>{md(x)}</li>" for x in m["주의"])

    body = f"""<div class="tabsec on" data-t="개요">
<div class="ex"><b>핵심</b> — {md(d["_핵심"])}</div>
<div class="card" style="border-left:4px solid var(--green)">
  <h3 style="margin-top:0">검증됨</h3><p>{md(d["_검증"])}</p></div>

<h2>내려받아야 할 데이터</h2>
<div class="card"><table><thead><tr><th style="width:44px">코드</th><th style="width:16%">이름</th>
<th style="width:30%">내용</th><th>용도</th><th style="width:10%">주기</th></tr></thead>
<tbody>{need}</tbody></table></div>

<h2>시스템별 경로</h2>{sysblocks}

<h2>{e(m["제목"])}</h2>
<div class="card"><ol class="steps">{steps}</ol>
<div class="warn"><b>주의</b><ul style="margin:6px 0 0 18px">{cautions}</ul></div></div>
</div>"""
    fake = {"code": "downloads", "name": "시스템 다운로드", "full": "월별 참여율·인건비를 뽑는 법",
            "역할": f'시스템 {len(d["시스템"])}종 · 확인 {d["확인일"]}', "과제": [], "site_menu": []}
    return page(cfg, fake, body,
                '<div class="sitemenu"><button class="on" data-t="개요">전체</button></div>')


def build_people(cfg) -> str:
    import datetime as dt
    d = json.loads((DATA / "people.json").read_text(encoding="utf-8"))
    pay, span = d["월편성_인건비"], d["과제기간"]
    ppl = d["인력"]

    # ── 과제별 참여인력 ──
    byproj = {}
    for x in ppl:
        for pr, r in x["현금참여율"].items():
            byproj.setdefault(pr, []).append((x, r, "현금"))
        for pr, r in x["현물참여율"].items():
            byproj.setdefault(pr, []).append((x, r, "현물"))
    sec1 = ""
    for pr in sorted(byproj, key=lambda k: -(pay.get(k) or 0)):
        rows = "".join(
            f'<tr><td><b>{e(x["이름"])}</b>{" <span style=color:var(--red)>퇴사</span>" if x["퇴사"] else ""}</td>'
            f'<td>{e(x["부서"])}</td><td>{e(x["직위"])}</td>'
            f'<td style="text-align:right">{r}%</td><td>{e(k)}</td></tr>'
            for x, r, k in sorted(byproj[pr], key=lambda t: -t[1]))
        tot = sum(r for _, r, _ in byproj[pr])
        m = pay.get(pr)
        sec1 += (f'<div class="card"><h3 style="margin-top:0">{e(pr)}</h3>'
                 f'<p style="color:var(--sub);font-size:13px">참여 {len(byproj[pr])}명 · 참여율 합 {tot}% · '
                 f'월편성 인건비 <b>{f"{m:,}" if m else "—"}</b>'
                 f'{" · " + e(span[pr][0]) + "~" + e(span[pr][1]) if pr in span else ""}</p>'
                 f'<table><thead><tr><th>이름</th><th>부서</th><th>직위</th>'
                 f'<th style="text-align:right">참여율</th><th>구분</th></tr></thead>'
                 f'<tbody>{rows}</tbody></table></div>')

    # ── 인력별 ──
    rows = ""
    for x in sorted(ppl, key=lambda z: -z["총참여율"]):
        pj = " ".join(f'<span class="tag">{e(k)} {v}%</span>' for k, v in x["현금참여율"].items())
        pj += " ".join(f'<span class="tag" style="background:#eaf4ff">{e(k)} {v}%(현물)</span>'
                       for k, v in x["현물참여율"].items())
        over = x["총참여율"] > 100
        src2 = str(x.get("출처", ""))
        sb2 = ("src-sys" if src2.startswith("시스템") else
               "src-wip" if src2.startswith("변경") else "src-sheet")
        l2 = "시스템" if sb2 == "src-sys" else ("변경중" if sb2 == "src-wip" else "시트")
        rows += (f'<tr><td><b>{e(x["이름"])}</b>'
                 f'<span class="src-b {sb2}">{l2}</span></td>'
                 f'<td>{e(x["부서"])}</td><td>{e(x["직위"])}</td>'
                 f'<td style="text-align:right">{x["소계_현금"]}%</td>'
                 f'<td style="text-align:right">{x["소계_현물"]}%</td>'
                 f'<td style="text-align:right">'
                 f'<b style="color:{"var(--red)" if over else "inherit"}">{x["총참여율"]}%</b></td>'
                 f'<td>{pj}</td>'
                 f'<td>{e(x["퇴사"]) if x["퇴사"] else ""}</td></tr>')
    sec2 = (f'<div class="card"><table><thead><tr><th>이름</th><th>부서</th><th>직위</th>'
            f'<th style="text-align:right">현금</th><th style="text-align:right">현물</th>'
            f'<th style="text-align:right">총</th><th>과제별</th><th>퇴사</th></tr></thead>'
            f'<tbody>{rows}</tbody></table></div>')

    # ── 월별 집행 계획 (매월 10일) ──
    def months(a, b):
        y, m = int(a[:4]), int(a[5:7]); Y, M = int(b[:4]), int(b[5:7])
        while (y, m) <= (Y, M):
            yield f"{y:04d}-{m:02d}"
            m += 1
            if m > 12:
                y, m = y + 1, 1
    projs = [k for k in pay if k in span]
    hdr = "".join(f"<th style='text-align:right'>{e(k.split('. ')[-1][:9])}</th>" for k in projs)
    body_rows, prev_year = "", None
    for ym in months("2026-08", "2027-12"):
        cells, tot = "", 0
        for k in projs:
            a, b = span[k]
            on = a <= ym <= b
            v = pay[k] if on else 0
            tot += v
            cells += (f"<td style='text-align:right'>{v:,}</td>" if v
                      else "<td style='text-align:right;color:#c3ced8'>—</td>")
        yr = ym[:4]
        if yr != prev_year:
            body_rows += (f"<tr><td colspan='{len(projs)+2}' style='background:#eef3f8;"
                          f"font-weight:700;color:var(--navy)'>{yr}년</td></tr>")
            prev_year = yr
        body_rows += (f"<tr><td><b>{ym[5:7]}월 10일</b></td>{cells}"
                      f"<td style='text-align:right;font-weight:700'>{tot:,}</td></tr>")
    sec3 = (f'<div class="card"><table><thead><tr><th>집행일</th>{hdr}'
            f'<th style="text-align:right">합계</th></tr></thead><tbody>{body_rows}</tbody></table>'
            f'<div class="note">{e(d["집행일"])}</div></div>')

    over = [x for x in ppl if x["총참여율"] > 100]
    left = [x for x in ppl if x["퇴사"]]
    warn = ""
    if over:
        warn += ('<div class="warn"><b>총 참여율이 100%를 넘는 인원 '
                 f'{len(over)}명</b> — ' + ", ".join(f'{e(x["이름"])} {x["총참여율"]}%' for x in over)
                 + '. 한 사람의 시간은 100%가 상한이다. 규정 위반인지, 현금·현물을 각각 적어 합계가 겹친 집계 방식 때문인지 '
                 '확인이 필요하다.</div>')
    if left:
        warn += ('<div class="miss"><b>퇴사·휴직인데 참여율이 남아 있는 인원 '
                 f'{len(left)}명</b> — ' + ", ".join(f'{e(x["이름"])}({e(x["퇴사"])}, {x["총참여율"]}%)' for x in left)
                 + '. <b>참여연구원 변경 신청</b>이 안 되어 있으면 인건비 집행이 막히거나 정산에서 문제가 된다.</div>')
    warn += (f'<div class="note">사람당 인건비 금액은 <b>개인 월급여</b>가 있어야 산출된다. '
             f'이 시트에는 없다. <code>data/salaries.local.json</code> 에 넣으면 자동 계산된다.</div>')

    body = (f'<div class="tabsec on" data-t="타임라인"><h2>월별 참여율 타임라인</h2>'
            f'<p class="lead">2026-01 ~ 2028-08. 과제 기간과 퇴사일을 반영해 월별로 펼쳤다. '
            f'<b>합계 행이 붉으면 그 달에 100%를 넘는다.</b></p>{_timeline(d)}</div>'
            f'<div class="tabsec" data-t="과제별"><h2>과제별 참여인력</h2>{sec1}</div>'
            f'<div class="tabsec" data-t="인력별"><h2>인력별 배분</h2>{warn}{sec2}</div>'
            f'<div class="tabsec" data-t="월별계획"><h2>월별 인건비 집행 계획</h2>'
            f'<p class="lead">매월 <b>10일</b> 집행 기준. 과제 기간이 끝나면 그 달부터 빠진다.</p>{sec3}</div>')
    fake = {"code": "people", "name": "참여인력·인건비", "full": "과제 참여율과 월 인건비 집행",
            "역할": f'인력 {len(ppl)}명 · 기준일 {d["기준일"]}', "과제": [], "site_menu": []}
    menu = ('<div class="sitemenu"><button class="on" data-t="타임라인">월별 참여율</button>'
            '<button data-t="과제별">과제별</button>'
            '<button data-t="인력별">인력별</button>'
            '<button data-t="월별계획">인건비 집행계획</button></div>')
    return page(cfg, fake, body, menu)


def build_system(cfg, y) -> str:
    f = DATA / "procedures" / f"{y['code']}.json"
    pj = json.loads(f.read_text(encoding="utf-8")) if f.exists() else {}
    procs = pj.get("procedures", [])
    desc = pj.get("menu_desc", {})
    counts = {}
    for pr in procs:
        counts[pr.get("menu", "")] = counts.get(pr.get("menu", ""), 0) + 1

    # ── 개요 탭 ──
    ov = '<div class="tabsec on" data-t="개요">'
    ov += '<div class="card"><table>'
    ov += f"<tr><th style='width:110px'>정식명</th><td>{e(y['full'])}</td></tr>"
    ov += f"<tr><th>운영</th><td>{e(y['운영'])}</td></tr>"
    u = (y.get("url") or "").split(" ")[0]
    if u.startswith("http"):
        ov += (f"<tr><th>접속</th><td><a href='{e(u)}' target='_blank' "
               f"style='color:var(--blue)'>{e(u)} ↗</a></td></tr>")
    ov += f"<tr><th>역할</th><td>{md(y['역할'])}</td></tr></table>"
    if y.get("주의"):
        ov += f'<div class="warn"><b>알아둘 것</b> {md(y["주의"])}</div>'
    ov += "</div>"

    if y["code"] == "rcms":
        ov += ('<div class="card"><h3 style="margin-top:0">상세 매뉴얼</h3>'
               '<p>RCMS는 이미 상세 대시보드가 있다. 다시 만들지 않고 그대로 들여왔다.</p>'
               '<p style="margin-top:10px"><a href="rcms_원본.html" '
               'style="color:var(--blue);font-weight:700">▶ RCMS 사용법 대시보드 열기</a></p></div>')

    if pj.get("업무흐름"):
        ov += ('<h2>업무 흐름</h2><div class="flow">' + "".join(
            f'<div class="stage"><div class="t">{e(st["stage"])}</div><ul>'
            + "".join(f"<li>{e(t)}</li>" for t in st["tasks"]) + "</ul></div>"
            for st in pj["업무흐름"]) + "</div>")
    for img in pj.get("images", []):
        ov += (f'<figure><img src="img/{e(img["file"])}" alt="">'
               f'<figcaption>{md(img.get("caption",""))}</figcaption></figure>')
    if not pj:
        ov += ('<div class="empty-note">이 시스템의 매뉴얼은 아직 정리 전이다. '
               '상단 메뉴는 실제 사이트 구조를 옮겨둔 것이다.</div>')
    ov += "</div>"

    # ── 메뉴별 탭 ──
    tabs = ov
    for m in y.get("site_menu", []):
        mine = [x for x in procs if x.get("menu") == m]
        tabs += f'<div class="tabsec" data-t="{e(m)}">'
        if desc.get(m):
            tabs += f'<p class="lead">{md(desc[m])}</p>'
        if mine:
            tabs += "".join(render_proc(x) for x in mine)
        else:
            tabs += ('<div class="empty-note">이 메뉴의 절차는 아직 정리 전이다.</div>')
        tabs += "</div>"
    return page(cfg, y, tabs, sitemenu(y, counts))


def main() -> int:
    global DATA, OUT
    import argparse
    ap = argparse.ArgumentParser(description="정부과제 시스템 매뉴얼 빌드 (JSON >> HTML)")
    ap.add_argument("--data", help="소스 폴더 (기본: <저장소>/data)")
    ap.add_argument("--out", help="출력 폴더 (기본: <저장소>/output)")
    a = ap.parse_args()
    if a.data:
        DATA = Path(a.data)
    if a.out:
        OUT = Path(a.out)
    cfg = json.loads((DATA / "systems.json").read_text(encoding="utf-8"))
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "index.html").write_text(build_index(cfg), encoding="utf-8")
    print("· index.html")
    (OUT / "accounts.html").write_text(build_accounts(cfg), encoding="utf-8")
    print("· accounts.html")
    (OUT / "people.html").write_text(build_people(cfg), encoding="utf-8")
    print("· people.html")
    (OUT / "downloads.html").write_text(build_downloads(cfg), encoding="utf-8")
    print("· downloads.html")
    for y in cfg["systems"]:
        if y["code"] == "rcms" and RCMS_SRC is not None and RCMS_SRC.exists():
            shutil.copy2(RCMS_SRC, OUT / "rcms_원본.html")
            print("· rcms_원본.html (기존 대시보드)")
        (OUT / f"{y['code']}.html").write_text(build_system(cfg, y), encoding="utf-8")
        print(f"· {y['code']}.html")
    for stale in ("ezone.html",):
        (OUT / stale).unlink(missing_ok=True)
    print(f"\n완료 → {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
