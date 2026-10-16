#!/usr/bin/env python3
"""태스크 원장 → **운영 OS 대시보드의 보조 뷰**.

    python3 scripts/tasks/build.py

이것은 대시보드를 **대체하지 않는다.** 정본은 `rpbd_운영OS_대시보드.html` 이고,
여기는 그 뒤에 붙는 상세 화면이다 — 필요할 때 열어 보는 곳.

그래서 기본 화면에는 **결정이 필요한 것만** 둔다. 막혔다고 다 이슈가 아니다.
「회신 대기」·「검토 중」까지 이슈로 올리면 **결정할 게 없는 항목이 결정 목록을 채워**
정작 대표님·부서 간에 정해야 할 일이 묻힌다. 원본 대시보드가 「이번 주 결정할 것 7건」
이라고 쓴 것과 같은 기준을 쓴다.

나머지는 접어 둔다. 지우지 않는다 — 보고 싶을 때 펼치면 된다.
"""
from __future__ import annotations

import datetime as dt
import html
import json
import re
from pathlib import Path

PROJ = Path(__file__).resolve().parents[2]
D = PROJ / "data" / "tasks"
OUT = PROJ / "output" / "태스크_상세.html"
LEDGER_DIR = PROJ / "data" / "records" / "project"

STREAM_LABEL = {"dashboard_seed": "대시보드", "weekly_report": "주간보고",
                "action_ledger": "회의록", "calendar": "캘린더",
                "messenger": "부서방", "mail": "메일", "drive": "산출물"}
CONF_LABEL = {"ledger": "원장", "observed": "관측", "inferred": "추정"}

# **대표님 판단이 필요한 것만** 여기 올린다.
#
# 관계자 간 논의가 풀리지 않으면 대표님께 이슈 리포팅으로 올라오고, 부서 간 의견 충돌도
# 마찬가지다. 즉 그 둘은 **대표님 결정으로 가는 과정**이지 그 자체가 결정 요청이 아니다.
# 과정까지 결정 목록에 세우면 정작 정해야 할 것이 묻힌다.
DECISION = {"대표님컨펌"}


def esc(s) -> str:
    return html.escape(str(s or ""))


def read(p: Path) -> list[dict]:
    if not p.exists():
        return []
    return [json.loads(x) for x in p.read_text(encoding="utf-8").splitlines() if x.strip()]


def project_names() -> dict[str, str]:
    files = sorted(LEDGER_DIR.glob("*.jsonl")) if LEDGER_DIR.is_dir() else []
    if not files:
        return {}
    out = {}
    for line in files[-1].read_text(encoding="utf-8").splitlines():
        if line.strip():
            p = json.loads(line).get("payload", {})
            if p.get("project_id"):
                out[p["project_id"]] = p.get("name_short") or p.get("name_official") or ""
    return out


CSS = """
:root{--bg:#f6f7fb;--fg:#14163a;--dim:#5b6178;--line:#e3e6f0;--card:#fff;
 --ok:#1a7f37;--warn:#b54708;--bad:#b42318;--accent:#108CEB;
 --font:"Paperlogy","페이퍼로지",system-ui,-apple-system,sans-serif}
@media (prefers-color-scheme:dark){:root{--bg:#0d1020;--fg:#eef0f8;--dim:#9aa2bd;
 --line:#242a44;--card:#151a30;--ok:#4ade80;--warn:#fbbf24;--bad:#f87171}}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--fg);font-family:var(--font);
 font-size:14px;line-height:1.6;-webkit-font-smoothing:antialiased}
main{max-width:1180px;margin:0 auto;padding:28px 24px 90px}
h1{font-size:22px;margin:0 0 4px}
.stamp{color:var(--dim);font-size:12.5px;margin-bottom:22px}
.badge{display:inline-block;padding:2px 9px;border-radius:99px;border:1px solid currentColor;
 font-size:11px;font-weight:700;margin-right:8px}
section{margin:26px 0}
.sec{display:flex;align-items:baseline;gap:9px;margin:0 0 12px;
 padding-bottom:7px;border-bottom:2px solid var(--line)}
.sec h2{font-size:15.5px;margin:0}
.sec .n{color:var(--dim);font-size:12.5px}
.sec .why{margin-left:auto;color:var(--dim);font-size:12px;font-weight:400}
ul.items{list-style:none;margin:0;padding:0;display:grid;gap:8px}
li.t{background:var(--card);border:1px solid var(--line);border-radius:11px;padding:11px 13px}
li.t.hot{border-left:3px solid var(--bad)}
li.t.warn{border-left:3px solid var(--warn)}
li.t.ok{border-left:3px solid var(--ok)}
.tl{display:flex;gap:8px;align-items:flex-start}
.tt{flex:1 1 auto}
.meta{margin-top:5px;color:var(--dim);font-size:11.5px;display:flex;flex-wrap:wrap;gap:5px 11px}
.chip{border:1px solid var(--line);border-radius:99px;padding:1px 8px;font-size:11px}
.chip.pid{border-color:var(--accent);color:var(--accent)}
.chip.due{border-color:var(--warn);color:var(--warn)}
.chip.over{border-color:var(--bad);color:var(--bad);font-weight:700}
.chip.who{border-color:var(--ok);color:var(--ok)}
details{background:var(--card);border:1px solid var(--line);border-radius:11px;padding:11px 13px}
summary{cursor:pointer;font-weight:600;font-size:13.5px;color:var(--dim)}
summary::marker{color:var(--dim)}
.empty{color:var(--dim);background:var(--card);border:1px dashed var(--line);
 border-radius:11px;padding:15px;text-align:center;font-size:13px}
table{width:100%;border-collapse:collapse;font-size:12.5px;background:var(--card);
 border:1px solid var(--line);border-radius:11px;overflow:hidden}
th,td{padding:7px 11px;text-align:left;border-bottom:1px solid var(--line)}
th{color:var(--dim);font-weight:600;font-size:11.5px}
tr:last-child td{border-bottom:none}
td.num{text-align:right;font-variant-numeric:tabular-nums}
"""


def chips(t: dict, names: dict, today: dt.date) -> str:
    out = []
    if t.get("project_id"):
        nm = names.get(t["project_id"], "")
        out.append(f'<span class="chip pid">{esc(t["project_id"])}'
                   + (f' · {esc(nm)}' if nm else "") + "</span>")
    if t.get("owner"):
        out.append(f'<span class="chip who">{esc(t["owner"])}</span>')
    if t.get("due"):
        try:
            d = dt.date.fromisoformat(t["due"][:10])
            left = (d - today).days
            cls = "over" if left < 0 else "due"
            txt = f'{t["due"][5:]} ' + (f"D+{-left}" if left < 0 else
                                        ("오늘" if left == 0 else f"D-{left}"))
            out.append(f'<span class="chip {cls}">{esc(txt)}</span>')
        except ValueError:
            out.append(f'<span class="chip due">{esc(t["due"])}</span>')
    if t.get("needs_check"):
        out.append(f'<span class="chip over">{esc(t["needs_check"])}</span>')
    src = (t.get("source") or {}).get("stream")
    out.append(f'<span class="chip">{esc(STREAM_LABEL.get(src, src))}'
               f' · {esc(CONF_LABEL.get(t.get("confidence"), ""))}</span>')
    return "".join(out)


def li(t: dict, names: dict, today: dt.date, cls: str = "") -> str:
    return (f'<li class="t {cls}"><div class="tl"><div class="tt">{esc(t["title"])}'
            f'<div class="meta">{chips(t, names, today)}</div></div></div></li>')


def section(title: str, why: str, rows: list[str], empty: str) -> str:
    body = f'<ul class="items">{"".join(rows)}</ul>' if rows \
        else f'<div class="empty">{esc(empty)}</div>'
    return (f'<section><div class="sec"><h2>{esc(title)}</h2>'
            f'<span class="n">{len(rows)}건</span>'
            f'<span class="why">{esc(why)}</span></div>{body}</section>')


def fold(title: str, rows: list[dict], names: dict, today: dt.date) -> str:
    """접어 두는 묶음. **필요할 때 펼쳐 보는 것**이지 항상 보여줄 것이 아니다."""
    if not rows:
        return ""
    items = "".join(li(t, names, today) for t in rows)
    return (f'<section><details><summary>{esc(title)} '
            f'<span class="n">{len(rows)}건</span></summary>'
            f'<ul class="items" style="margin-top:10px">{items}</ul></details></section>')


def main() -> int:
    today = dt.date.today()
    now = dt.datetime.now()
    tasks = read(D / "ledger.jsonl")
    names = project_names()

    def needs(t):
        return (t.get("issue") or {}).get("needs")

    check = [t for t in tasks if t.get("needs_check") or t.get("confidence") == "inferred"]
    decide = [t for t in tasks if needs(t) in DECISION]
    week = [t for t in tasks if t["state"] in ("planned", "doing")]
    done = [t for t in tasks if t["state"] == "done"]
    # 결정 요청이 아닌 막힘 — 이슈 목록에 올리지 않고 접어 둔다
    waiting = [t for t in tasks if t["state"] == "blocked" and needs(t) not in DECISION]

    week.sort(key=lambda t: (t.get("due") or "9999", t["title"]))
    # 대표님 판단이 맨 위. 그다음이 부서 간, 그다음이 관계자 간이다.
    decide.sort(key=lambda t: (t.get("due") or "9999", t["title"]))

    overdue = [t for t in week if t.get("due") and t["due"][:10] < today.isoformat()]
    soon = [t for t in week if t not in overdue and t.get("due")
            and t["due"][:10] <= (today + dt.timedelta(days=7)).isoformat()]
    rest = [t for t in week if t not in overdue and t not in soon]

    by_pid: dict[str, list] = {}
    for t in tasks:
        by_pid.setdefault(t.get("project_id") or "—", []).append(t)
    rows = []
    for pid, ts in sorted(by_pid.items()):
        c = {k: sum(1 for t in ts if t["state"] == k) for k in ("planned", "blocked", "done")}
        rows.append(f'<tr><td>{esc(pid)}</td><td>{esc(names.get(pid, ""))}</td>'
                    f'<td class="num">{c["planned"]}</td><td class="num">{c["blocked"]}</td>'
                    f'<td class="num">{c["done"]}</td><td class="num">{len(ts)}</td></tr>')

    doc = f"""<!doctype html>
<html lang="ko"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>운영 OS · 태스크 상세</title><style>{CSS}</style></head><body><main>
<h1>운영 OS · 태스크 상세</h1>\n<p style="margin:0 0 4px"><a href="rpbd_운영OS_대시보드.html" style="color:var(--accent);text-decoration:none">← 운영 OS 대시보드로</a></p>
<div class="stamp">
  <span class="badge" id="fresh" data-at="{now:%Y-%m-%dT%H:%M}"></span>
  <b>운영 OS 대시보드의 보조 화면</b>입니다 · 태스크 {len(tasks)}건 ·
  원천 주간보고·회의록·캘린더 · 완료분은 화요일 보고 후 내려갑니다
</div>

{section("확인 필요", "추정 상태 — 사람 판단을 기다리는 항목입니다. 비어 있으면 아래를 믿어도 됩니다",
         [li(t, names, today, "hot") for t in check],
         "확인을 기다리는 항목이 없습니다. 아래 내용은 모두 원장·관측 근거가 있습니다.")}

{section("결정 필요", "대표님 판단이 있어야 다음으로 넘어갑니다",
         [li(t, names, today, "hot") for t in decide], "결정을 기다리는 항목이 없습니다.")}

{section("기한", f"지난 것 {len(overdue)}건 · 7일 안 {len(soon)}건",
         [li(t, names, today, "warn" if t in overdue else "") for t in overdue + soon],
         "기한이 걸린 항목이 없습니다.")}

{fold("진행 중 (기한 8일 이후 · 기한 없음)", rest, names, today)}
{fold("부서 간 · 관계자 간 조율 · 대기 (대표님 결정 전 단계)", waiting, names, today)}
{fold("완료 · 화요일 보고 후 내려감", done, names, today)}

<section><div class="sec"><h2>과제별 요약</h2><span class="n">{len(by_pid)}개</span></div>
<table><thead><tr><th>과제</th><th>이름</th><th>계획</th><th>이슈</th><th>완료</th><th>합계</th></tr></thead>
<tbody>{"".join(rows)}</tbody></table></section>
</main>
<script>
// 생성 시각을 적어 두는 대신 **열 때마다 얼마나 지났는지 다시 계산한다.**
// 이 파일은 드라이브에 놓여 며칠씩 그대로 열릴 수 있어, 배지가 스스로 늙어야 한다.
// 본문 맨 끝에 둔다 — 섹션 사이에 두면 화면을 손볼 때 같이 잘려 나간다(실제로 한 번 그랬다).
(function () {{
  var e = document.getElementById('fresh');
  if (!e) return;
  var m = Math.floor((Date.now() - new Date(e.dataset.at)) / 60000);
  e.textContent = m < 90 ? '방금 갱신'
    : (m < 1440 ? Math.floor(m / 60) + '시간 전' : Math.floor(m / 1440) + '일 지남');
  e.style.color = m < 90 ? 'var(--ok)' : (m < 1440 ? 'var(--warn)' : 'var(--bad)');
}})();
</script>
</body></html>
"""
    OUT.write_text(doc, encoding="utf-8")
    print(f"· {OUT.relative_to(PROJ)} — {len(doc) / 1024:,.0f}KB")
    print(f"  확인필요 {len(check)} · 결정필요 {len(decide)} · "
          f"기한(지남 {len(overdue)}·임박 {len(soon)}) · "
          f"접힘(진행 {len(rest)}·대기 {len(waiting)}·완료 {len(done)})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
