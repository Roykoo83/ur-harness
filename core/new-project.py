#!/usr/bin/env python3
"""신규 과제를 인프라(작업공간)와 SSOT(정본)에 한 번에 개설한다.

한 번의 실행으로:
  1. {{WORKSPACE_NAME}}/projects/<slug>/          — 세션 작업공간 (GOAL.md, RULES.md)
  2. projects/<slug>/.claude/settings.local.json
       — SSOT 과제 폴더를 additionalDirectories 로 등록.
         이 폴더에서 세션을 열면 오른쪽 패인에 SSOT 과제 폴더가 항상 함께 보인다.
  3. {{SSOT_DRIVE}}/<category>/<과제명>/  — 정본 폴더 골격 + _README.md (팀원용 소비 맥락)

원칙: **작업 산출물은 3번(SSOT)에 둔다** — 조사·검증·발주문·실행로그까지.
       팀원이 이어받으려면 로컬에만 있으면 안 된다 (2026-09-21 Roy 결정).
       1번(작업공간)에는 임시 스크립트·캐시·빌드 부산물만. U드라이브 원천은 읽기 전용.

사용:
    new-project.py <slug> "<SSOT 과제 폴더명>" [--category <경로>] [--dry-run]

예:
    new-project.py abc-project "ABC 프로젝트 (수요기관)" \
        --category "{{DEPT_ROOT}}/10_과제/2_지원중"

기존 파일은 절대 덮어쓰지 않는다 (있으면 건너뛰고 보고만 한다).
"""
import argparse
import datetime
import json
import os
import sys
import unicodedata
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import works_env  # noqa: E402  (같은 폴더에 함께 배포된다)

works_env.setup_stdout()
REPO_BASE = works_env.repo_base()
SSOT_BASE = works_env.find_ssot()
DEFAULT_CATEGORY = "{{DEPT_ROOT}}/10_과제/1_수행중"
SSOT_SUBDIRS = ["00_기준문서", "수행"]

GOAL_TEMPLATE = """# {slug} — 과제 작업 공간

**{title}**

| 항목 | 값 |
|---|---|
| 부처 / 전문기관 | (채우기) |
| 주관 / 참여 | (채우기) |
| 기간 | (채우기) |
| 어반 사업비 | (채우기) |
| 참여 | (채우기) |

## 지금 상태 ({today})

- 과제 개설 (스캐폴드 자동 생성) — 표의 (채우기) 항목부터 확정할 것

## 원천 자료 (읽기 전용 — 여기로 복사하지 않는다)

| 무엇 | 어디 |
|---|---|
| 원천 문서 | U드라이브 (채우기) |
| SSOT 정본 | `{{SSOT_DRIVE}}/{category}/{title}/` |
| 태스크 원장 | `~/{{WORKSPACE_NAME}}/projects/rpbd-infra/data/tasks/ledger.jsonl` |

## 산출물 규칙

- **작업 산출물은 SSOT 과제 폴더에 둔다** — 조사 결과·검증 기록·워커 발주문·실행로그까지
- 이 폴더에는 임시 스크립트·캐시만 남긴다
- SSOT에 올릴 때 `_README.md`(팀원용 안내)를 함께 갱신한다
"""

RULES_TEMPLATE = """# {slug} 작업 규칙

1. SSOT·U드라이브 원천은 **읽기 전용** — 수정·삭제 금지 (C7.2)
2. 태스크 상태 변경은 `rpbd-infra/scripts/tasks/apply.py` 로 — 원장을 직접 고치지 않는다
3. 금액·용어는 **주간보고서 → 온톨로지 → 메일 → 전사** 우선순위로 확인
4. 발송(메일·봇)은 승인된 유형만 — 신규 유형은 Roy 승인 후
"""

README_TEMPLATE = """# {title} — 과제 폴더 안내

> 이 폴더에 **작업 산출물이 전부 있습니다** — 정본 문서와 그 근거(조사 원본·발주문)까지.
> 처음 오신 분은 이 문서 하나만 읽으면 됩니다.

## 과제 한눈에

| 항목 | 내용 |
|---|---|
| 사업명 | {title} |
| 부처 / 전문기관 | (채우기) |
| 주관 / 참여 | (채우기) |
| 기간 | (채우기) |
| 어반 담당 | (채우기) |

## 폴더 안내

| 폴더 | 내용 |
|---|---|
| `00_기준문서/` | 공고문 · 안내서 · 관리기준 — 사업의 규칙 원문 |
| `수행/` | 협약서 · 수행계획서 · 공문 — 수행의 법적 기준 |

## 꼭 알아야 할 규약

1. 파일 추가·수정은 연구기획사업부를 통해서만 합니다 — 직접 넣지 마세요 (확정본 보증 유지).
2. 회계·집행·예산·증빙 자료는 이 폴더에 두지 않고 원천(U드라이브)에서 별도 관리합니다.

---
- 관리: 연구기획사업부 (claudex 이관 시 자동 갱신)
- 최종 갱신: {today}
"""


CLAUDE_TEMPLATE = """# {title} — 세션 진입점

이 파일은 이 폴더에서 세션을 열면 자동으로 읽힌다. **읽을 순서와 지금 급한 것만** 둔다.
과제 내용은 `GOAL.md`, 작업 규칙은 `RULES.md`에 있다.

## 세션 시작 절차

1. `~/{{WORKSPACE_NAME}}/CONSTITUTION.md` · `RULES.md` (마스터 규약)
2. 이 폴더의 `GOAL.md` (과제 개요·지금 상태) >> `RULES.md` (이 과제만의 규칙)
3. 직전 세션 인계: (첫 세션 이후 이 줄에 인계 문서 경로를 적는다)
4. 복귀 브리핑이 필요하면 `python3 ~/{{WORKSPACE_NAME}}/scripts/works.py brief "{slug}"`

## 지금 급한 것

| 순위 | 건 | 상태 |
|---|---|---|
| 1 | (채우기 — 세션을 닫을 때마다 갱신한다) | |

## 위치

- 작업·초안: 이 폴더 (`작업/` · `workspace/` · `output/`)
- **SSOT 정본**: `{{SSOT_DRIVE}}/{category}/{title}/` (세션 패인에 함께 열린다)
- 태스크 원장: `~/{{WORKSPACE_NAME}}/projects/rpbd-infra/data/tasks/ledger.jsonl`

## 주의

- SSOT·U드라이브 원천은 **읽기 전용**. 확정본 이관 외의 쓰기는 하지 않는다 (C7.2)
- `GOAL.md`의 수치를 인용하기 전에 기준일을 확인한다
- em dash 금지 (`:` · `>>` · `-` 로 대체)

---
생성: {today} (`new-project.py`)
"""

SSOT_CLAUDE_TEMPLATE = """# {title} — SSOT 과제 폴더 부트스트랩

이 폴더는 **SSOT 정본 폴더**다. **작업 산출물도 여기 둔다** — 조사 결과·검증 기록·워커 발주문·실행로그까지.
팀원이 이어받을 수 있어야 하기 때문이다. 임시 스크립트·캐시만 작업공간에 남긴다.
이 파일은 이 폴더를 컨텍스트로 열면 자동으로 읽힌다. 사람용 안내는 `_README.md`에 있다.

## 여기서 무엇을 하나

| 하는 일 | 어디에 |
|---|---|
| 기준 확인 (공고문·관리기준·협약) | `00_기준문서/` · `수행/` |
| 조사·분석 산출물 | `수행/30_*/` — 근거 원본 · `_발주문/` · `_실행로그/` |
| 정본 문서 (옮겨 쓸 것) | `수행/_*.md` |
| 확정본 보관 | 이 폴더의 용도별 하위 폴더 |

### 정본과 근거를 구분한다 (분리하지 않는다)

- `수행/_*.md` = **정본.** 검증을 거쳐 옮겨 쓸 수 있는 것
- `수행/30_*/` = **근거.** 워커 원본. 정본이 가리키는 출처
- 근거에 틀린 수치가 있어도 지우지 않는다. **정본에서 정정 사실을 명시**한다
- 작업공간 `~/{{WORKSPACE_NAME}}/projects/{slug}/`에는 임시 스크립트·캐시만 남긴다

### 워커를 쓰면

발주문과 실행로그를 `수행/30_*/_발주문/` · `_실행로그/`에 남긴다.
**무엇을 시켰는지 남아야 다음 사람이 결과를 믿을 수 있다.**

## 업무 시작 절차

1. 이번에 처리할 업무 1건을 정한다
2. **그 업무의 기존 산출물을 먼저 찾는다** — 이 폴더에 이미 있는지 확인한다.
   버전이 있으면 최신본을 기준으로 삼는다 (있는 것을 다시 만들지 않는다)
3. AX 판정: 기준을 글로 쓸 수 있는가 · 결과를 검증할 수 있는가.
   둘 다 아니면 AI에 넘기지 않고 사람이 한다
4. 작업지시문 4요소를 채운다 — [원본](읽기 전용) · [규칙] · [산출물] · [검증]
5. 끝나면 무엇을 했고 다음이 무엇인지 3~5줄로 남긴다

## 지키는 것

- 이 폴더와 U드라이브 원천은 **읽기 전용 취급** — 파일 추가는 확정본 이관 절차로만 (C7.2)
- 회계·집행·예산·증빙은 이 폴더에 두지 않는다 (별도 관리)
- 규범 전문: `50_도구상자/00_규범/` 5건
- AI가 틀렸으면 그 실수를 규칙 1줄로 이 파일에 추가한다

---
생성: {today} (`new-project.py`)
"""


def nfc(s: str) -> str:
    return unicodedata.normalize("NFC", s)


def plan_files(slug: str, title: str, category: str) -> list[tuple[Path, str]]:
    today = datetime.date.today().isoformat()
    ws = REPO_BASE / "projects" / slug
    ssot = SSOT_BASE / category / nfc(title)
    settings = json.dumps(
        {"permissions": {"additionalDirectories": [str(ssot)]}},
        ensure_ascii=False, indent=2,
    ) + "\n"
    files = [
        (ssot / "_README.md", README_TEMPLATE.format(title=title, today=today)),
        (ssot / "CLAUDE.md", SSOT_CLAUDE_TEMPLATE.format(
            slug=slug, title=title, today=today)),
    ]
    # repo 가 없는 맥(부서원)에서는 작업공간을 만들지 않는다 — 엉뚱한 곳에 폴더가 생긴다
    if REPO_BASE.is_dir():
        # SSOT 쪽 역링크: SSOT 루트로 세션을 열어도(works.py open) repo 가 함께 열린다
        ssot_settings = json.dumps(
            {"permissions": {"additionalDirectories": [str(REPO_BASE)]}},
            ensure_ascii=False, indent=2,
        ) + "\n"
        files = [
            (ws / "GOAL.md", GOAL_TEMPLATE.format(
                slug=slug, title=title, category=category, today=today)),
            (ws / "RULES.md", RULES_TEMPLATE.format(slug=slug)),
            (ws / "CLAUDE.md", CLAUDE_TEMPLATE.format(
                slug=slug, title=title, category=category, today=today)),
            (ws / ".claude" / "settings.local.json", settings),
            (ssot / ".claude" / "settings.local.json", ssot_settings),
        ] + files
    return files


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("slug", help="작업공간 폴더명 (영문 kebab-case, 예: abc-project)")
    ap.add_argument("title", help="SSOT 과제 폴더명 (한글 공식명)")
    ap.add_argument("--category", default=DEFAULT_CATEGORY,
                    help=f"SSOT 내 분류 경로 (기본: {DEFAULT_CATEGORY})")
    ap.add_argument("--dry-run", action="store_true", help="생성하지 않고 계획만 출력")
    args = ap.parse_args()

    if not SSOT_BASE.exists():
        print("오류: SSOT 드라이브를 찾지 못했습니다.", file=sys.stderr)
        print(works_env.ssot_help(SSOT_BASE), file=sys.stderr)
        return 1

    ssot_dir = SSOT_BASE / args.category / nfc(args.title)
    dirs = [ssot_dir / d for d in SSOT_SUBDIRS]
    files = plan_files(args.slug, args.title, args.category)

    created, skipped = [], []
    for d in dirs:
        if args.dry_run:
            created.append(f"[dir]  {d}")
            continue
        d.mkdir(parents=True, exist_ok=True)
        created.append(f"[dir]  {d}")
    for path, content in files:
        if path.exists():
            skipped.append(f"[skip] {path} (이미 있음, 덮어쓰지 않음)")
            continue
        if args.dry_run:
            created.append(f"[file] {path}")
            continue
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        created.append(f"[file] {path}")

    tag = "(dry-run) " if args.dry_run else ""
    print(f"{tag}신규 과제 개설: {args.slug} ↔ {args.title}")
    for line in created + skipped:
        print("  " + line)
    print("\n다음 할 일:")
    if REPO_BASE.is_dir():
        print("  1. GOAL.md의 (채우기) 항목 확정")
        print("  2. TASKS.md에 과제 개설 등록")
        print(f"  3. python3 {REPO_BASE}/scripts/works.py open \"{args.slug}\"  ← SSOT 루트 워크스페이스로 열린다")
    else:
        print("  작업공간(repo)이 없어 SSOT 골격만 만들었습니다.")
        print("  1. SSOT 과제 폴더의 `_README.md` (채우기) 항목 확정")
        print(f"  2. 그 폴더를 세션에 붙여 작업: /add-dir '{ssot_dir}'")
    return 0


if __name__ == "__main__":
    sys.exit(main())
