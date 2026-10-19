#!/usr/bin/env python3
"""회의록 마크다운을 컨플루언스 페이지에 올린다.

    python3 scripts/publish_minutes.py <회의록.md> <페이지ID>

**마크다운 원본과 컨플루언스 게시본은 범위가 다르다.**
`향후 일정`은 우리 태스크 원장이 읽는 절이라 원본에는 반드시 남겨야 하지만,
전사 공유본에는 담당·기한이 붙은 내부 추적표까지 올릴 필요가 없다(운영자 지시 2026-08-25).
그래서 **원본은 건드리지 않고 게시할 때만 걷어낸다.**

교체 경계는 `📝 회의록` 제목이다. HTML 주석 표식은 컨플루언스 편집기가 저장할 때
지워 버려서(실측), 사람이 페이지를 한 번 손대면 다음 업로드가 사본을 하나 더 붙인다.
"""
from __future__ import annotations

import pathlib
import re
import sys

HERE = pathlib.Path(__file__).resolve().parent
REPO = HERE.parents[2]
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "dashboard"))
sys.path.insert(0, str(HERE))   # md2conf 는 이 폴더에 있다 (2026-09-08: /tmp 에 두어 유실됐던 것을 이설)

# 컨플루언스 게시본에서 뺄 절 (제목으로 지정)
SKIP_SECTIONS = ("향후 일정",)
ANCHOR = re.compile(r'<h2[^>]*>\s*(?:<[^>]+>)*\s*📝\s*회의록.*$', re.S)


def strip_sections(md: str, titles) -> str:
    """`## n. <제목>` 절을 통째로 뺀다. 뒤 절 번호는 손대지 않는다 —
    원본과 번호가 어긋나면 어느 절이 빠졌는지 되레 알기 어려워진다."""
    out, skip = [], False
    for line in md.split("\n"):
        m = re.match(r"^##\s+\d+\.\s*(.+)$", line)
        if m:
            skip = any(t in m.group(1) for t in titles)
        if not skip:
            out.append(line)
    return "\n".join(out)


def main() -> int:
    src, pid = pathlib.Path(sys.argv[1]), sys.argv[2]
    import md2conf
    import confluence_client as C
    from confluence_client import BASE
    import requests

    md = strip_sections(src.read_text(encoding="utf-8"), SKIP_SECTIONS)
    body_add = md2conf.convert(md)

    cli = C.get_client(allow_browser=False)
    cur = cli.get_json(f"/wiki/rest/api/content/{pid}?expand=version,body.storage")
    ver, body = cur["version"]["number"], cur["body"]["storage"]["value"]
    new = ANCHOR.sub("", body) + "<h2>📝 회의록</h2>" + body_add

    r = requests.put(f"{BASE}/wiki/rest/api/content/{pid}",
                     headers={"Authorization": cli._auth, "Content-Type": "application/json"},
                     json={"version": {"number": ver + 1, "message": "회의록 갱신 (자동)"},
                           "title": cur["title"], "type": "page",
                           "body": {"storage": {"value": new, "representation": "storage"}}},
                     timeout=90)
    if r.status_code >= 400:
        print(f"실패 {r.status_code}: {r.text[:200]}")
        return 1
    print(f"· 게시 완료 — 버전 {r.json()['version']['number']} · 제외 절 {', '.join(SKIP_SECTIONS)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
