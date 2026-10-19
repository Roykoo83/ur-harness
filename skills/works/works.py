#!/usr/bin/env python3
"""works — 프로젝트 작업 워크플로우의 조회·복귀·기동 도구.

`/works` 스킬이 인터뷰 중에 호출한다. 세 가지만 한다:

    works.py list                     SSOT 과제 목록 + 작업공간 연결 여부
    works.py brief <키워드>            복귀 브리핑 (어디까지 진행됐는지)
    works.py bootstrap <키워드>        진입점 CLAUDE.md 2종을 실제 상태로 채워 생성
    works.py desktop <키워드>          Claude Desktop 프로젝트 세팅 묶음 (B-1)
    works.py open <slug> [--title T]  작업 폴더 준비 (+ 설정된 작업창 열기)

신규 과제 개설은 `new-project.py` 소관이다. 여기서 중복 구현하지 않는다.
SSOT·U드라이브는 읽기 전용으로만 접근한다 (stat 우선, 본문 읽기는 실패 허용).
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import unicodedata
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import works_env  # noqa: E402  (같은 폴더에 함께 배포된다)

works_env.setup_stdout()
REPO = works_env.repo_base()
SSOT = works_env.find_ssot()
TASK_ROOT = SSOT / "{{DEPT_ROOT}}" / "10_과제"
CATEGORIES = ["0_제안", "1_수행중", "2_실주", "3_종료·정산", "4_인증·기타"]
MAIL_STAGE = REPO / "projects" / "nworks-agent" / "output" / "stage" / "mail_{{BOT_USER}}"
STOPWORDS = {"사업", "과제", "용역", "연계", "지원", "개발", "구축", "고도화", "기타"}


def nfc(s: str) -> str:
    return unicodedata.normalize("NFC", s)


def norm(s: str) -> str:
    """비교용 정규화. `AX-SPRINT` 와 `AX_Sprint` 가 같게 걸려야 한다."""
    return re.sub(r"[-_·\s.]+", "", nfc(s).lower())


def tokens(name: str) -> list[str]:
    """과제명에서 검색 키워드를 뽑는다. 로그·태스크·메일 매칭에 쓴다."""
    parts = re.split(r"[\s()·,\[\]/]+", nfc(name))
    out = []
    for p in parts:
        p = p.strip("_-.")
        if len(p) >= 3 and p not in STOPWORDS and not p.isdigit():
            out.append(p)
    return out


def workspace_map() -> dict[str, Path]:
    """SSOT 과제 폴더 >> 작업공간. settings.local.json 의 등록을 역인덱스한다."""
    out: dict[str, Path] = {}
    root = REPO / "projects"
    if not root.is_dir():
        return out
    for ws in sorted(root.iterdir()):
        cfg = ws / ".claude" / "settings.local.json"
        if not cfg.is_file():
            continue
        try:
            dirs = json.loads(cfg.read_text(encoding="utf-8")).get(
                "permissions", {}).get("additionalDirectories", [])
        except (OSError, ValueError):
            continue
        for d in dirs:
            out[nfc(Path(d).name)] = ws
    return out


def ssot_projects() -> list[dict]:
    """SSOT 과제 목록. 본문을 읽지 않고 디렉터리만 훑는다 (dataless 안전)."""
    wsmap = workspace_map()
    rows = []
    for cat in CATEGORIES:
        base = TASK_ROOT / cat
        if not base.is_dir():
            continue
        for p in sorted(base.iterdir()):
            if not p.is_dir() or p.name.startswith("_"):
                continue
            rows.append({
                "category": cat,
                "title": nfc(p.name),
                "ssot": p,
                "workspace": wsmap.get(nfc(p.name)),
            })
    return rows


def cmd_list(args: argparse.Namespace) -> int:
    rows = ssot_projects()
    if not rows:
        print("SSOT 과제 폴더를 찾지 못했습니다.")
        print(works_env.ssot_help(SSOT))
        return 1
    n = 0
    cur = None
    for r in rows:
        if r["category"] != cur:
            cur = r["category"]
            print(f"\n[{cur}]")
        n += 1
        ws = r["workspace"]
        mark = f"작업공간 {ws.name}" if ws else "작업공간 없음"
        print(f"  {n:2}. {r['title']}  ({mark})")
    print(f"\n총 {n}건 · 작업공간 연결 "
          f"{sum(1 for r in rows if r['workspace'])}건")
    return 0


def pick(keyword: str) -> dict | None:
    kw = nfc(keyword).lower()
    rows = ssot_projects()
    for r in rows:
        if kw == r["title"].lower() or (r["workspace"] and kw == r["workspace"].name.lower()):
            return r
    hits = [r for r in rows
            if kw in r["title"].lower()
            or (r["workspace"] and kw in r["workspace"].name.lower())]
    if len(hits) == 1:
        return hits[0]
    if len(hits) > 1:
        print("여러 건이 걸립니다. 더 구체적으로 지정하세요:", file=sys.stderr)
        for h in hits:
            print(f"  - {h['title']}", file=sys.stderr)
    return None


def section(path: Path, header: str, limit: int = 14) -> list[str]:
    """마크다운에서 특정 절만 뽑는다. 읽기 실패는 조용히 넘긴다."""
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeDecodeError):
        return []
    out, on = [], False
    for ln in lines:
        if ln.startswith("#"):
            if on:
                break
            on = header in ln
            continue
        if on and ln.strip():
            out.append(ln.rstrip())
            if len(out) >= limit:
                break
    return out


def task_rows(path: Path, keys: list[str], limit: int = 4) -> list[str]:
    """TASKS.md 에서 관련 행을 뽑아 ID·제목·담당까지만 남긴다 (행이 매우 길다)."""
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeDecodeError):
        return []
    nkeys = [norm(k) for k in keys if len(k) >= 3]
    out = []
    for ln in lines:
        if not any(k in norm(ln) for k in nkeys):
            continue
        cells = [c.strip() for c in ln.split("|") if c.strip()]
        out.append(" · ".join(cells[:4])[:220] if len(cells) >= 3 else ln.strip()[:220])
        if len(out) >= limit:
            break
    return out


def recent_files(base: Path, keys: list[str] | None = None,
                 limit: int = 5, suffix: str = "",
                 maxdepth: int = 7, cap: int = 6000) -> list[tuple[float, Path]]:
    """mtime 내림차순. stat 만 쓰므로 클라우드 전용 파일에도 안전하다.

    클라우드 마운트는 순회 자체가 느리다(과제 폴더 하나가 500건을 넘는다).
    깊이와 방문 파일 수에 상한을 둔다 — 브리핑은 최근 몇 건만 보면 된다.
    """
    found: list[tuple[float, Path]] = []
    if not base.is_dir():
        return found
    nkeys = [norm(k) for k in (keys or []) if len(k) >= 3]
    seen = 0
    for root, dirs, files in os.walk(base):
        depth = len(Path(root).relative_to(base).parts)
        dirs[:] = [] if depth >= maxdepth else [d for d in dirs if not d.startswith(".")]
        if seen >= cap:
            break
        for f in files:
            seen += 1
            if f.startswith(".") or f in {"Icon\r", "desktop.ini"}:
                continue
            if suffix and not f.endswith(suffix):
                continue
            if nkeys and not any(k in norm(f) for k in nkeys):
                continue
            p = Path(root) / f
            try:
                found.append((p.stat().st_mtime, p))
            except OSError:
                continue
    found.sort(reverse=True)
    return found[:limit]


def cmd_brief(args: argparse.Namespace) -> int:
    r = pick(args.keyword)
    if not r:
        print(f"'{args.keyword}' 에 해당하는 과제를 찾지 못했습니다. "
              f"`works.py list` 로 확인하세요.", file=sys.stderr)
        return 1

    keys = tokens(r["title"]) + ([r["workspace"].name] if r["workspace"] else [])
    if args.kw:
        keys += args.kw
    ws = r["workspace"]

    print(f"# 복귀 브리핑: {r['title']}\n")
    print(f"- 분류: {r['category']}")
    print(f"- SSOT: `{r['ssot']}`")
    print(f"- 작업공간: {'`' + str(ws) + '`' if ws else '**없음** (신규 개설 필요)'}")
    print(f"- 검색 키워드: {', '.join(keys[:6])}")

    missing = []
    if ws and not (ws / "CLAUDE.md").is_file():
        missing.append(f"작업공간 진입점 `{ws.name}/CLAUDE.md`")
    if not (r["ssot"] / "CLAUDE.md").is_file():
        missing.append("SSOT 폴더 부트스트랩 `CLAUDE.md`")
    if missing:
        print(f"- ⚠ 진입점 없음: {' · '.join(missing)} "
              f"(`new-project.py` 를 같은 인자로 다시 돌리면 빠진 것만 생긴다)")

    if ws:
        state = section(ws / "GOAL.md", "지금 상태")
        print("\n## 지금 상태 (GOAL.md)\n")
        print("\n".join(state) if state else "(GOAL.md에 「지금 상태」 절이 없습니다)")

    print("\n## 태스크 원장 (TASKS.md)\n")
    hits = task_rows(REPO / "TASKS.md", keys, limit=3)
    if hits:
        for h in hits:
            print(f"- {h}")
    else:
        print("(해당 과제로 등록된 행이 없습니다)")

    print("\n## 최근 작업 로그\n")
    logs = recent_files(REPO / "logs", keys, limit=4, suffix=".md")
    if ws:
        logs += recent_files(ws / "작업", None, limit=3, suffix=".md")
    if logs:
        for mt, p in sorted(logs, reverse=True)[:5]:
            import datetime
            d = datetime.date.fromtimestamp(mt).isoformat()
            print(f"- {d} `{p.relative_to(REPO) if REPO in p.parents else p.name}`")
    else:
        print("(관련 로그 없음)")

    print("\n## SSOT 최근 변경\n")
    changed = recent_files(r["ssot"], None, limit=5)
    if changed:
        for mt, p in changed:
            import datetime
            d = datetime.date.fromtimestamp(mt).isoformat()
            print(f"- {d} `{p.relative_to(r['ssot'])}`")
    else:
        print("(변경 이력 없음 또는 폴더 비어 있음)")

    print("\n## 최근 관련 메일\n")
    mails = recent_files(MAIL_STAGE, keys, limit=5, suffix=".md")
    if mails:
        for mt, p in mails:
            print(f"- `{p.parent.name}/{p.name}`")
    else:
        print("(제목에 키워드가 걸린 메일 없음 — 본문 검색은 별도로 하세요)")

    print("\n## SSOT 폴더 구성\n")
    subs = sorted(p.name for p in r["ssot"].iterdir()
                  if p.is_dir() and not p.name.startswith("."))
    print(" · ".join(f"`{s}/`" for s in subs) if subs else "(하위 폴더 없음)")
    print("\n**업무를 정한 뒤 그 업무에 해당하는 폴더를 먼저 열어 기존 산출물을 확인하세요.**")
    print("위 「최근 변경」은 5건만 보여주므로 몇 달 전 산출물은 걸리지 않습니다.")

    print("\n---\n다음: 이 브리핑을 확인한 뒤 이번 세션에 처리할 업무 1건을 정합니다.")
    return 0


DESKTOP_GUIDE = """이 프로젝트의 정본은 SSOT `{rel}` 이다.
원본은 읽기 전용이다. 확정본만 그 폴더에 올리고 초안은 대화에 둔다.
규범은 SSOT `50_도구상자/00_규범/` 5건을 따른다.

초안은 대화에 두고, 사람이 확정한 것만 정본 폴더에 올린다. 확정 전 파일을 올리지 않는다.

업무를 시작할 때 다음 순서로 진행한다.

1. 이번에 처리할 업무 1건을 정한다.
2. **그 업무의 기존 산출물을 먼저 찾는다.** 정본 폴더에 이미 있는지 확인하고,
   버전이 있으면 최신본을 기준으로 삼는다. 있는 것을 다시 만들지 않는다.
   `★기준본` · `_인계노트` · `_WORKER_LOG` 로 시작하는 파일이 있으면 먼저 읽는다.
3. AX 판정: 기준을 글로 쓸 수 있는가 · 결과를 검증할 수 있는가.
   둘 다 아니면 AI에 넘기지 않고 사람이 한다.
4. 작업지시문 4요소를 채운 뒤 시작한다.
   [무엇을] <업무> 를 처리한다
   [원본]   <경로> (읽기 전용, 수정 금지)
   [규칙]   <지킬 것 2~3개> / <하지 말 것 1~2개>
   [산출물] <경로/파일명>
   [검증]   <어떻게 확인하면 되는지 1줄>
5. 끝나면 무엇을 했고 다음이 무엇인지 3~5줄로 남긴다. 다음 대화가 그것부터 읽는다.

AI가 틀렸으면 그 실수를 규칙 1줄로 이 지침에 추가한다."""


ANCHOR_MARKS = ("★", "_인계노트", "_worker_log", "기준본", "_handoff")


def anchor_docs(base: Path, limit: int = 4) -> list[Path]:
    """기준본·인계노트처럼 '먼저 읽어야 하는' 문서를 찾는다."""
    out = []
    for mt, p in recent_files(base, limit=400, suffix=".md"):
        n = p.name.lower()
        if any(m in n or m in p.name for m in ANCHOR_MARKS):
            out.append(p)
            if len(out) >= limit:
                break
    return out


def cmd_bootstrap(args: argparse.Namespace) -> int:
    """과제의 실제 상태로 진입점 2종을 채운다. 빈 템플릿을 주지 않는다."""
    r = pick(args.keyword)
    if not r:
        print(f"'{args.keyword}' 에 해당하는 과제를 찾지 못했습니다.", file=sys.stderr)
        return 1

    ssot, ws = r["ssot"], r["workspace"]
    rel = ssot.relative_to(SSOT)
    keys = tokens(r["title"]) + ([ws.name] if ws else [])
    today = __import__("datetime").date.today().isoformat()

    anchors = anchor_docs(ssot)
    subs = sorted(p.name for p in ssot.iterdir()
                  if p.is_dir() and not p.name.startswith("."))
    changed = recent_files(ssot, limit=5)
    logs = recent_files(REPO / "logs", keys, limit=3, suffix=".md")
    state = section(ws / "GOAL.md", "지금 상태") if ws else []

    def block(paths, base, empty="(없음)"):
        if not paths:
            return empty
        return "\n".join(f"- `{p.relative_to(base)}`" for p in paths)

    # 이 문서는 SSOT 에 올라가 모두가 본다 — 이 맥에만 있는 경로를 넣지 않는다
    self_path = Path(__file__).resolve()
    try:
        self_ref = "~/" + str(self_path.relative_to(Path.home()))
    except ValueError:
        self_ref = str(self_path)

    if REPO.is_dir():
        steps = ["`~/{{WORKSPACE_NAME}}/CONSTITUTION.md` · `RULES.md` (마스터 규약)"]
    else:
        steps = ["SSOT `50_도구상자/00_규범/` 5건 (헌법·작업규칙·업무절차·AI세션규칙·에이전트규약)"]
    if ws:
        steps.append(f"`{ws}/GOAL.md` >> `RULES.md`")
    if anchors:
        steps.append("**먼저 읽을 문서** (이미 확인된 사실이 들어 있어 재조사 불필요)\n"
                     + "\n".join(f"   - `{p.relative_to(ssot)}`" for p in anchors))
    steps.append(f"복귀 브리핑: `python3 {self_ref} brief \"{args.keyword}\"`")
    read_order = [f"{i}. {s}" for i, s in enumerate(steps, 1)]

    common = f"""## 세션 시작 절차

{chr(10).join(read_order)}

## 최근 상태 ({today} 기준 자동 수집)

**SSOT 최근 변경**
{block([p for _, p in changed], ssot)}

**작업 로그**
{block([p for _, p in logs], REPO, "(관련 로그 없음)")}
"""
    if state:
        common += "\n**GOAL 「지금 상태」**\n" + "\n".join(state[:8]) + "\n"
    common += ("\n> 위 수집분은 생성 시점 기준입니다. 세션을 닫을 때 「지금 급한 것」을 사람이 갱신하고,\n"
               "> 오래되었다고 판단되면 `works.py brief` 로 다시 뽑습니다.\n")

    ws_doc = f"""# {r['title']} — 세션 진입점

작업·초안은 이 폴더에, **확정본만** SSOT 정본 폴더로 올립니다.
SSOT 정본: `{{SSOT_DRIVE}}/{rel}/` (세션 패인에 함께 열립니다)

{common}
## 지금 급한 것

| 순위 | 건 | 상태 |
|---|---|---|
| 1 | (세션을 닫을 때마다 갱신합니다) | |

## 주의

- SSOT·U드라이브 원천은 **읽기 전용**. 확정본 이관 외의 쓰기는 하지 않습니다 (C7.2)
- 수치를 인용하기 전에 기준일을 확인합니다
- em dash 금지 (`:` · `>>` · `-` 로 대체)

---
생성: {today} (`works.py bootstrap`)
"""

    ssot_doc = f"""# {r['title']} — SSOT 과제 폴더 부트스트랩

이 폴더는 **정본 폴더**입니다. 확정본만 둡니다. 사람용 안내는 `_README.md` 에 있습니다.
{f"작업공간: `{ws}` (초안은 그곳에)" if ws else
 "**작업공간이 없습니다.** 작업공간을 쓸 수 없는 환경(Claude Desktop 등)이라면 "
 "초안은 대화에 두고, 사람이 확정한 것만 이 폴더에 올립니다."}

## 이 폴더 구성

{" · ".join(f"`{s}/`" for s in subs) if subs else "(하위 폴더 없음)"}

{common}
## 업무 시작 절차

1. 이번에 처리할 업무 1건을 정합니다
2. **그 업무에 해당하는 위 폴더를 먼저 열어 기존 산출물을 확인합니다.**
   버전이 있으면 최신본을 기준으로 삼습니다. 있는 것을 다시 만들지 않습니다
3. AX 판정: 기준을 글로 쓸 수 있는가 · 결과를 검증할 수 있는가.
   둘 다 아니면 AI에 넘기지 않고 사람이 합니다
4. 작업지시문 4요소: [원본](읽기 전용) · [규칙] · [산출물] · [검증]
5. 끝나면 무엇을 했고 다음이 무엇인지 3~5줄로 남깁니다

## 지키는 것

- 이 폴더와 U드라이브 원천은 **읽기 전용 취급** — 파일 추가는 확정본 이관 절차로만 (C7.2)
- 회계·집행·예산·증빙은 이 폴더에 두지 않습니다 (별도 관리)
- 규범 전문: `50_도구상자/00_규범/` 5건
- AI가 틀렸으면 그 실수를 규칙 1줄로 이 파일에 추가합니다

---
생성: {today} (`works.py bootstrap`)
"""

    targets = [(ssot / "CLAUDE.md", ssot_doc)]
    if ws:
        targets.insert(0, (ws / "CLAUDE.md", ws_doc))

    if not args.apply:
        print(f"(미리보기) {r['title']} — 먼저 읽을 문서 {len(anchors)}건 · "
              f"하위 폴더 {len(subs)}개 · 최근 변경 {len(changed)}건 반영\n")
        for p, body in targets:
            if p.is_file():
                print(f"{'='*70}\n[이미 있음 — 건너뜁니다] {p}\n")
                continue
            print(f"{'='*70}\n[새로 만듭니다] {p}\n{'='*70}")
            print(body)
        print("실제로 만들려면 `--apply` 를 붙이세요. 기존 파일은 덮어쓰지 않습니다.")
        return 0

    for p, body in targets:
        if p.is_file():
            print(f"[skip] {p} (이미 있음)")
            continue
        try:
            p.write_text(body, encoding="utf-8")
        except OSError as e:
            print(f"[실패] {p}: {e}", file=sys.stderr)
            continue
        print(f"[생성] {p}")
    return 0


def cmd_desktop(args: argparse.Namespace) -> int:
    """Claude Desktop 프로젝트에 붙여넣을 세팅 묶음을 만든다 (B-1)."""
    r = pick(args.keyword)
    if not r:
        print(f"'{args.keyword}' 에 해당하는 과제를 찾지 못했습니다.", file=sys.stderr)
        return 1
    rel = r["ssot"].relative_to(SSOT)

    print(f"# Claude Desktop 세팅: {r['title']}\n")
    print("## 1. 프로젝트 만들기\n")
    print(f"Claude Desktop에서 Project를 하나 만들고 이름을 이렇게 둡니다.\n")
    print(f"    {r['title']}\n")
    print("## 2. 프로젝트 지침에 붙여넣기\n")
    print("```")
    print(DESKTOP_GUIDE.format(rel=rel))
    print("```\n")
    print("## 3. 지식으로 첨부할 파일\n")
    picks = [p for _, p in recent_files(r["ssot"] / "00_기준문서", limit=6)]
    readme = r["ssot"] / "_README.md"
    if readme.is_file():
        picks.insert(0, readme)
    if picks:
        for p in picks:
            print(f"- `{p.relative_to(SSOT)}`")
    else:
        print("- (기준문서가 아직 없습니다. `_README.md` 부터 만드세요)")
    print(f"\n원본 위치: `{r['ssot']}`")
    print("\n첨부는 사람이 앱에서 올립니다. Desktop은 터미널이 없어 자동 세팅이 되지 않습니다.")
    return 0


def cmd_open(args: argparse.Namespace) -> int:
    r = pick(args.slug)
    if not r:
        print(f"SSOT 과제를 찾지 못했습니다: {args.slug}\n"
              f"신규는 `new-project.py <slug> \"<과제명>\"` 으로 먼저 개설하세요.",
              file=sys.stderr)
        return 1
    # 패인 루트 = SSOT 정본 폴더 (운영자 표준 2026-09-07). 작업공간은 역링크로 함께 열린다.
    ssot = r["ssot"]
    cfg = ssot / ".claude" / "settings.local.json"
    make_cfg = not cfg.is_file()
    if not r["workspace"]:
        print("주의: 연결된 작업공간이 없습니다. 초안 작업 전 new-project.py 로 개설하세요.",
              file=sys.stderr)
    title = args.title or r["title"]

    # 작업창을 여는 방법은 사람마다 다르다 — 터미널 멀티플렉서를 쓰는 사람도 있고,
    # 데스크톱 앱에서 폴더를 여는 사람도 있다. 그래서 **기본값을 두지 않고**
    # 설정에 있을 때만 그 명령을 부른다. 없으면 열 폴더를 알려 준다.
    #   harness.yaml:
    #     workspace:
    #       open_command: "<내 도구> --name {title} --cwd {path}"
    opener = _open_command()

    if args.dry_run:
        if make_cfg:
            print(f"[만들 예정] {cfg} (저장소 역링크)")
        if opener:
            print(" ".join(opener).format(title=title, path=str(ssot)))
        else:
            print(f"[열 폴더] {ssot}")
        return 0

    if make_cfg:
        cfg.parent.mkdir(exist_ok=True)
        cfg.write_text(json.dumps(
            {"permissions": {"additionalDirectories": [str(REPO)]}},
            ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(f"[생성] {cfg} (저장소 역링크)")

    if not opener:
        print(f"\n이 폴더를 AI 클라이언트에서 엽니다:\n  {ssot}\n")
        print("  · 데스크톱 앱: Code 탭 → 그 폴더 열기")
        print("  · 터미널: 그 폴더로 이동한 뒤 실행")
        print("\n자동으로 열고 싶으면 harness.yaml 의 workspace.open_command 를 채웁니다.")
        return 0

    cmd = [c.format(title=title, path=str(ssot)) for c in opener]
    try:
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.SubprocessError) as e:
        # 열지 못한 것을 성공으로 치지 않는다. 폴더 경로는 알려 준다
        print(f"작업창 열기 실패: {e}", file=sys.stderr)
        print(f"이 폴더를 직접 엽니다: {ssot}", file=sys.stderr)
        return 1
    print((out.stdout or out.stderr).strip())
    return out.returncode


def _open_command() -> list[str] | None:
    """작업창을 여는 명령. 설정에 없으면 None — 기본값을 두지 않는다.

    `{title}` 과 `{path}` 자리표시자를 쓴다.
    """
    try:
        import shlex                                          # noqa: PLC0415
        import sys as _s                                      # noqa: PLC0415
        from pathlib import Path as _P                        # noqa: PLC0415
        _s.path.insert(0, str(_P(__file__).resolve().parents[2] / "core"))
        from lib.config import cfg as _cfg                    # noqa: PLC0415
        v = _cfg("workspace.open_command")
        return shlex.split(v) if v else None
    except Exception:                                         # noqa: BLE001
        return None


def main() -> int:
    ap = argparse.ArgumentParser(prog="works.py", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    sub.add_parser("list", help="SSOT 과제 목록 + 작업공간 연결 여부")

    b = sub.add_parser("brief", help="복귀 브리핑 (어디까지 진행됐는지)")
    b.add_argument("keyword", help="과제명 조각 또는 작업공간 slug")
    b.add_argument("--kw", nargs="*", help="검색 키워드 추가")

    bs = sub.add_parser("bootstrap", help="진입점 CLAUDE.md 2종을 실제 상태로 채워 생성")
    bs.add_argument("keyword", help="과제명 조각 또는 작업공간 slug")
    bs.add_argument("--apply", action="store_true", help="실제로 파일을 만든다")

    d = sub.add_parser("desktop", help="Claude Desktop 프로젝트 세팅 묶음 (B-1)")
    d.add_argument("keyword", help="과제명 조각 또는 작업공간 slug")

    o = sub.add_parser("open", help="작업 폴더 준비 (SSOT 루트) + 설정된 작업창 열기")
    o.add_argument("slug", help="과제명 조각 또는 작업공간 slug")
    o.add_argument("--title", help="워크스페이스 표시 이름")
    o.add_argument("--dry-run", action="store_true")

    args = ap.parse_args()
    return {"list": cmd_list, "brief": cmd_brief, "bootstrap": cmd_bootstrap,
            "desktop": cmd_desktop, "open": cmd_open}[args.cmd](args)


if __name__ == "__main__":
    sys.exit(main())
