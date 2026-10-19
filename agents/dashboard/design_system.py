"""{{ORG_NAME}} Data Lab 대시보드 공통 디자인 시스템.

RPBD 운영 OS 대시보드(`rpbd-infra/output/rpbd_운영OS_대시보드.html`)와 동일한 토큰을 쓴다.
값을 바꾸면 양쪽이 함께 바뀌므로, 토큰은 design.md §3.1을 벗어나지 않는다.
"""

CSS = """
:root{
  --brand-mint:#14DEAF; --brand-teal:#33C3C6; --brand-blue:#108CEB; --brand-purple:#745CED;
  --brand-deep:#0A082B;
  --font-primary:"Paperlogy","페이퍼로지",system-ui,sans-serif;
  --font-secondary:"Freesentation",ui-monospace,monospace;
  --radius-md:14px; --radius-pill:999px;
  --gradient-brand:linear-gradient(90deg,#14DEAF 0%,#33C3C6 34%,#108CEB 67%,#745CED 100%);
}
:root,[data-theme="dark"]{
  color-scheme:dark;
  --canvas:#0A082B; --surface-1:rgba(255,255,255,.06); --surface-2:rgba(255,255,255,.10);
  --text-primary:#FFFFFF; --text-secondary:rgba(255,255,255,.72); --text-tertiary:rgba(255,255,255,.52);
  --border:rgba(144,192,240,.24); --grid-line:rgba(144,192,240,.16);
  --shadow-card:0 24px 56px -20px rgba(0,0,80,.72);
  --accent:#9B8AF2; --accent-wash:rgba(155,138,242,.12); --accent-edge:rgba(155,138,242,.44);
  --warn:#FF8A6B; --warn-wash:rgba(255,138,107,.12);
  --ok:#14DEAF; --ok-wash:rgba(20,222,175,.10);
  --info:#5080F0; --info-wash:rgba(80,128,240,.12);
}
[data-theme="light"]{
  color-scheme:light;
  --canvas:#FFFFFF; --surface-1:rgba(10,8,43,.04); --surface-2:rgba(16,140,235,.08);
  --text-primary:#0A082B; --text-secondary:rgba(10,8,43,.72); --text-tertiary:rgba(10,8,43,.52);
  --border:rgba(10,8,43,.16); --grid-line:rgba(10,8,43,.10);
  --shadow-card:0 24px 56px -20px rgba(0,0,80,.28);
  --accent:#5F45D6; --accent-wash:rgba(95,69,214,.08); --accent-edge:rgba(95,69,214,.38);
  --warn:#C2410C; --warn-wash:rgba(194,65,12,.10);
  --ok:#0E9F7E; --ok-wash:rgba(14,159,126,.10);
  --info:#2563EB; --info-wash:rgba(37,99,235,.10);
}
*{box-sizing:border-box}
html{scroll-behavior:smooth}
body{margin:0;background:var(--canvas);color:var(--text-primary);
  font-family:var(--font-primary);-webkit-font-smoothing:antialiased;line-height:1.55}
body::before{content:"";position:fixed;inset:0;z-index:0;pointer-events:none;
  background:radial-gradient(900px 520px at 12% -6%,rgba(20,222,175,.10),transparent 62%),
             radial-gradient(760px 480px at 92% 4%,rgba(116,92,237,.12),transparent 60%)}
.wrap{position:relative;z-index:1;max-width:1400px;margin:0 auto;padding:0 40px 100px}
@media (max-width:900px){.wrap{padding:0 18px 70px}}

header.top{position:sticky;top:0;z-index:50;background:color-mix(in srgb,var(--canvas) 90%,transparent);
  backdrop-filter:blur(14px);border-bottom:1px solid var(--border)}
.top-in{max-width:1400px;margin:0 auto;padding:13px 40px 0;display:flex;flex-direction:column;gap:9px}
@media (max-width:900px){.top-in{padding:12px 18px 0}}
.top-row{display:flex;align-items:baseline;justify-content:space-between;gap:16px;flex-wrap:wrap}
.brand{display:flex;align-items:center;gap:12px}
.brand .mark{width:26px;height:26px;flex:none}
.brand h1{margin:0;font-size:19px;font-weight:800;letter-spacing:-.01em}
.brand .sub{font:600 11px/1 var(--font-secondary);letter-spacing:.12em;color:var(--text-tertiary);text-transform:uppercase}
.stamp{font:600 12px/1.55 var(--font-secondary);color:var(--text-tertiary);text-align:right}
.stamp b{color:var(--text-secondary);font-weight:700}
nav.tabs{display:flex;gap:3px;overflow-x:auto;padding-bottom:2px;scrollbar-width:none;align-items:center}
nav.tabs::-webkit-scrollbar{display:none}
nav.tabs a{flex:none;text-decoration:none;color:var(--text-tertiary);font:700 13px/1 var(--font-primary);
  padding:11px 13px;border-bottom:2px solid transparent;white-space:nowrap;transition:.15s}
nav.tabs a:hover{color:var(--text-secondary)}
nav.tabs a.on{color:var(--text-primary);border-bottom-color:var(--accent)}
.themebtn{flex:none;margin-left:auto;background:var(--surface-1);border:1px solid var(--border);
  color:var(--text-secondary);border-radius:var(--radius-pill);padding:6px 14px;
  font:700 12px var(--font-primary);cursor:pointer}

.hero{padding:28px 0 6px}
.hero h2{margin:0 0 6px;font-size:30px;font-weight:800;letter-spacing:-.02em}
.hero p{margin:0;color:var(--text-secondary);font-size:14.5px;max-width:88ch}
.hero p b{color:var(--text-primary)}
.kpis{display:grid;grid-template-columns:repeat(4,1fr);gap:12px;margin-top:22px}
@media (max-width:1000px){.kpis{grid-template-columns:repeat(2,1fr)}}
.kpi{background:var(--surface-1);border:1px solid var(--border);border-radius:var(--radius-md);
  padding:15px 18px;position:relative;overflow:hidden}
.kpi::before{content:"";position:absolute;left:0;top:0;bottom:0;width:3px;background:var(--gradient-brand)}
.kpi .v{font:800 26px/1 var(--font-secondary);letter-spacing:-.02em}
.kpi .l{margin-top:7px;font-size:12px;color:var(--text-tertiary);font-weight:600}
.kpi.warn .v{color:var(--warn)}

section{padding-top:52px;scroll-margin-top:116px}
.sec-head{display:flex;align-items:flex-end;gap:13px;flex-wrap:wrap;
  padding-bottom:11px;border-bottom:2px solid var(--border);margin-bottom:20px}
.sec-num{font:800 13px/1 var(--font-secondary);color:var(--canvas);background:var(--gradient-brand);
  padding:7px 11px;border-radius:8px;letter-spacing:.04em}
.sec-head h3{margin:0;font-size:22.5px;font-weight:800;letter-spacing:-.015em}
.sec-head .desc{margin-left:auto;font-size:12.5px;color:var(--text-tertiary);font-weight:600}

.cards{display:grid;grid-template-columns:1fr 1fr;gap:16px}
@media (max-width:1120px){.cards{grid-template-columns:1fr}}
.card{background:var(--surface-1);border:1px solid var(--border);border-radius:var(--radius-md);
  padding:19px 21px;display:flex;flex-direction:column;gap:13px;box-shadow:var(--shadow-card)}
.card.wide{grid-column:1/-1}
.card-head{display:flex;align-items:flex-start;gap:10px;flex-wrap:wrap}
.card-head .id{font:700 10.5px/1.6 var(--font-secondary);color:var(--text-tertiary);letter-spacing:.06em}
.card-head h4{margin:2px 0 0;font-size:17px;font-weight:800;letter-spacing:-.01em}
.card-head .meta{width:100%;font-size:11.5px;color:var(--text-tertiary);font-weight:600;margin-top:1px}
.chip{font:700 10.5px/1 var(--font-secondary);letter-spacing:.05em;padding:5px 9px;border-radius:var(--radius-pill);
  border:1px solid var(--border);color:var(--text-secondary);background:var(--surface-2);white-space:nowrap;flex:none}
.chip.hot{color:var(--warn);border-color:color-mix(in srgb,var(--warn) 44%,transparent);background:var(--warn-wash)}
.chip.ok{color:var(--ok);border-color:color-mix(in srgb,var(--ok) 44%,transparent);background:var(--ok-wash)}
.chip.new{color:var(--info);border-color:color-mix(in srgb,var(--info) 50%,transparent);background:var(--info-wash)}

.block{border-top:1px solid var(--grid-line);padding-top:11px}
.block:first-of-type{border-top:0;padding-top:0}
.block-t{display:flex;align-items:center;gap:8px;margin-bottom:9px;
  font:700 11px/1 var(--font-secondary);letter-spacing:.1em;color:var(--text-tertiary);text-transform:uppercase}
.block-t .dot{width:6px;height:6px;border-radius:50%;background:var(--accent);flex:none}
.block-t.issue .dot{background:var(--warn)}

ul.items{list-style:none;margin:0;padding:0;display:flex;flex-direction:column;gap:8px}
ul.items li{display:flex;gap:9px;font-size:13.5px;color:var(--text-secondary);line-height:1.5}
ul.items li b{color:var(--text-primary);font-weight:700}
.tag{flex:none;height:20px;border-radius:5px;display:grid;place-items:center;margin-top:1px;
  font:800 10.5px/1 var(--font-secondary);width:22px}
.tag.p{background:var(--ok-wash);color:var(--ok);border:1px solid color-mix(in srgb,var(--ok) 36%,transparent)}
.tag.f{background:var(--info-wash);color:var(--info);border:1px solid color-mix(in srgb,var(--info) 40%,transparent)}
.tag.c{background:var(--warn-wash);color:var(--warn);border:1px solid color-mix(in srgb,var(--warn) 40%,transparent);width:auto;padding:0 6px}
.tag.d{background:var(--accent-wash);color:var(--accent);border:1px solid var(--accent-edge);width:auto;padding:0 6px}
.src{font:600 10.5px/1.5 var(--font-secondary);color:var(--text-tertiary);margin-top:2px;display:block}

.tablewrap{overflow-x:auto;border:1px solid var(--border);border-radius:var(--radius-md);background:var(--surface-1)}
table{border-collapse:collapse;width:100%;min-width:660px;font-size:12.8px}
th,td{text-align:left;padding:10px 13px;border-bottom:1px solid var(--grid-line);vertical-align:top}
th{font:700 11px/1.4 var(--font-secondary);letter-spacing:.06em;color:var(--text-tertiary);text-transform:uppercase;
   background:var(--surface-2);white-space:nowrap}
td{color:var(--text-secondary)}
td b{color:var(--text-primary);font-weight:700}
tr:last-child td{border-bottom:0}
.pending{color:var(--text-tertiary);font-style:italic}
.kind{font:800 10px/1 var(--font-secondary);padding:4px 7px;border-radius:5px;white-space:nowrap;display:inline-block}
.kind.sales{background:var(--ok-wash);color:var(--ok)}
.kind.rnd{background:var(--info-wash);color:var(--info)}
.kind.ra{background:var(--accent-wash);color:var(--accent)}
.kind.etc{background:var(--surface-2);color:var(--text-tertiary)}
.kind.ax{background:var(--warn-wash);color:var(--warn)}

/* 부서 그리드 (색인 페이지) */
.orgs{display:grid;grid-template-columns:repeat(auto-fill,minmax(300px,1fr));gap:16px}
.org{display:block;text-decoration:none;color:inherit;background:var(--surface-1);
  border:1px solid var(--border);border-radius:var(--radius-md);padding:20px 22px;transition:.15s}
.org:hover{border-color:var(--accent-edge);transform:translateY(-2px)}
.org h4{margin:0 0 4px;font-size:18px;font-weight:800}
.org .n{font:800 24px/1 var(--font-secondary);margin-top:12px}
.org .l{font-size:12px;color:var(--text-tertiary);font-weight:600;margin-top:5px}
.org.missing{border-style:dashed;opacity:.7}

/* 주차 추이 스파크라인 */
.spark{display:flex;gap:3px;align-items:flex-end;height:34px;margin-top:12px}
.spark i{flex:1;background:var(--accent-wash);border-top:2px solid var(--accent);border-radius:2px 2px 0 0;min-height:3px}
.spark i.zero{background:var(--warn-wash);border-top-color:var(--warn)}

.note{margin-top:13px;font-size:12.5px;color:var(--text-tertiary);background:var(--surface-1);
  border:1px dashed var(--border);border-radius:var(--radius-md);padding:12px 16px}
.note b{color:var(--text-secondary)}
footer{margin-top:66px;padding-top:20px;border-top:1px solid var(--border);
  font:600 11.5px/1.75 var(--font-secondary);color:var(--text-tertiary)}
"""

LOGO = """<svg class="mark" viewBox="0 0 32 24" fill="none" aria-hidden="true">
  <defs><linearGradient id="g" x1="0" y1="0" x2="32" y2="24" gradientUnits="userSpaceOnUse">
    <stop stop-color="#14DEAF"/><stop offset=".5" stop-color="#108CEB"/><stop offset="1" stop-color="#745CED"/>
  </linearGradient></defs>
  <path d="M4 4v9a7 7 0 0 0 14 0V4" stroke="url(#g)" stroke-width="3" stroke-linecap="round"/>
  <circle cx="25" cy="8" r="4" stroke="url(#g)" stroke-width="2.4"/>
</svg>"""

THEME_BTN = ('<button class="themebtn" onclick="var r=document.documentElement;'
             "r.dataset.theme=r.dataset.theme==='dark'?'light':'dark'\">◐ 테마</button>")


def page(title: str, subtitle: str, stamp: str, tabs: str, body: str,
         footer: str) -> str:
    return f"""<!doctype html>
<html lang="ko" data-theme="dark">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title}</title>
<style>{CSS}</style>
</head>
<body>
<header class="top">
  <div class="top-in">
    <div class="top-row">
      <div class="brand">{LOGO}
        <div><div class="sub">{{ORG_NAME}} Data Lab</div><h1>{subtitle}</h1></div>
      </div>
      <div class="stamp">{stamp}</div>
    </div>
    <nav class="tabs">{tabs}{THEME_BTN}</nav>
  </div>
</header>
<div class="wrap">
{body}
<footer>{footer}</footer>
</div>
</body>
</html>
"""
