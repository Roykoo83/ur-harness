#!/usr/bin/env python3
"""마크다운 >> Confluence storage format. 회의록에 쓰는 부분집합만 다룬다.

지원: h2~h4 · 표 · 불릿(2단) · 번호목록 · 인용 · 구분선 · 굵게 · 코드 · 취소선
"""
from __future__ import annotations
import html
import re
import sys
from pathlib import Path


def inline(s: str) -> str:
    s = html.escape(s, quote=False)
    s = re.sub(r"~~(.+?)~~", r"<s>\1</s>", s)
    s = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", s)
    s = re.sub(r"`([^`]+)`", r"<code>\1</code>", s)
    return s


def convert(md: str) -> str:
    out: list[str] = []
    lines = md.splitlines()
    i = 0
    while i < len(lines):
        ln = lines[i]
        s = ln.strip()

        if not s:
            i += 1
            continue

        if s == "---":
            out.append("<hr />")
            i += 1
            continue

        m = re.match(r"^(#{2,4})\s+(.*)$", s)
        if m:
            level = len(m.group(1)) + 1          # ## >> h3, ### >> h4, #### >> h5
            out.append(f"<h{level}>{inline(m.group(2))}</h{level}>")
            i += 1
            continue

        # 표 — 헤더 + 구분행 + 데이터행
        if s.startswith("|") and i + 1 < len(lines) and re.match(r"^\|[\s:|-]+\|$", lines[i + 1].strip()):
            def cells(row: str) -> list[str]:
                return [c.strip() for c in row.strip().strip("|").split("|")]
            head = cells(s)
            i += 2
            rows = []
            while i < len(lines) and lines[i].strip().startswith("|"):
                rows.append(cells(lines[i].strip()))
                i += 1
            t = ["<table><tbody><tr>"]
            t += [f"<th>{inline(c)}</th>" for c in head]
            t.append("</tr>")
            for r in rows:
                t.append("<tr>" + "".join(f"<td>{inline(c)}</td>" for c in r) + "</tr>")
            t.append("</tbody></table>")
            out.append("".join(t))
            continue

        # 인용 (연속 줄 합침)
        if s.startswith(">"):
            buf = []
            while i < len(lines) and lines[i].strip().startswith(">"):
                buf.append(lines[i].strip()[1:].strip())
                i += 1
            out.append(f"<blockquote><p>{inline(' '.join(buf))}</p></blockquote>")
            continue

        # 번호 목록
        if re.match(r"^\d+\.\s", s):
            items = []
            while i < len(lines) and re.match(r"^\d+\.\s", lines[i].strip()):
                body = re.sub(r"^\d+\.\s+", "", lines[i].strip())
                i += 1
                # 이어지는 들여쓴 줄을 같은 항목으로
                while i < len(lines) and lines[i].startswith("   ") and lines[i].strip() \
                        and not re.match(r"^\d+\.\s", lines[i].strip()) and not lines[i].strip().startswith("-"):
                    body += " " + lines[i].strip()
                    i += 1
                items.append(body)
            out.append("<ol>" + "".join(f"<li><p>{inline(x)}</p></li>" for x in items) + "</ol>")
            continue

        # 불릿 (2단까지)
        if s.startswith("- "):
            top: list[tuple[str, list[str]]] = []
            while i < len(lines):
                raw = lines[i]
                st = raw.strip()
                if not st:
                    break
                indent = len(raw) - len(raw.lstrip())
                if st.startswith("- ") and indent == 0:
                    top.append((st[2:], []))
                    i += 1
                elif st.startswith("- ") and indent >= 2 and top:
                    top[-1][1].append(st[2:])
                    i += 1
                elif indent >= 2 and top:                 # 이어지는 줄
                    if top[-1][1]:
                        top[-1][1][-1] += " " + st
                    else:
                        top[-1] = (top[-1][0] + " " + st, top[-1][1])
                    i += 1
                else:
                    break
            parts = ["<ul>"]
            for text, subs in top:
                parts.append(f"<li><p>{inline(text)}</p>")
                if subs:
                    parts.append("<ul>" + "".join(f"<li><p>{inline(x)}</p></li>" for x in subs) + "</ul>")
                parts.append("</li>")
            parts.append("</ul>")
            out.append("".join(parts))
            continue

        # 일반 문단 (빈 줄까지 합침)
        buf = [s]
        i += 1
        while i < len(lines) and lines[i].strip() and not re.match(r"^(#{2,4}\s|\||>|-\s|\d+\.\s|---$)", lines[i].strip()):
            buf.append(lines[i].strip())
            i += 1
        out.append(f"<p>{inline(' '.join(buf))}</p>")

    return "".join(out)


if __name__ == "__main__":
    src = Path(sys.argv[1])
    body = convert(src.read_text(encoding="utf-8"))
    header = ("<hr /><h2>📝 회의록</h2><h2>전사 주간회의 (2026-09-08)</h2>"
              "<blockquote><p><strong>사내한정</strong> &middot; 계약·매출·고객사 정보가 포함됩니다. "
              "외부 공유는 하지 않습니다.</p></blockquote>")
    Path(sys.argv[2]).write_text(header + body, encoding="utf-8")
    print(f"변환 완료: {len(header + body):,}자")
