#!/usr/bin/env python3
"""익명화·비밀값 스캐너 — 공개 저장소에 올리기 전 반드시 통과해야 하는 게이트.

이 저장소는 **공개**다. 한 번 올라간 것은 지워도 캐시·포크에 남는다.
그래서 커밋 전에 이 스캐너가 0건을 보고해야 한다.

무엇을 잡나
  1 개인 식별자   사람 이름 · 개인 계정 · 조직 고유명
  2 환경 고유값   개인 홈 경로 · 드라이브 ID · 채널 ID
  3 비밀값        API 키 접두 · PEM 블록 · 토큰 리터럴
  4 연락처        이메일 · 전화번호

무엇을 잡지 않나 (의도적 허용)
  · `UR` — 제품 이름이다
  · 제품·서비스명(네이버웍스 · Google Drive · Confluence · Fathom · Groq) —
    연동 대상을 밝히는 것이 이 저장소의 목적이다
  · `example.org` · `<...>` 꼴 자리표시자

사용
  scan.py              현재 폴더를 훑고 결과를 표로 낸다
  scan.py --json       기계용 출력
  scan.py --fix-hint   위반마다 치환 제안을 붙인다
종료 코드 0 = 깨끗 · 1 = 위반 있음 · 2 = 실행 오류
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

# ── 스캔 대상 ────────────────────────────────────────────────────────────────
TEXT_SUFFIX = {
    ".py", ".sh", ".bash", ".zsh", ".md", ".txt", ".json", ".yaml", ".yml",
    ".toml", ".ini", ".cfg", ".conf", ".html", ".css", ".js", ".mjs", ".ts",
    ".xml", ".plist", ".service", ".timer", ".env", ".example", ".tsv", ".csv",
}
SKIP_DIR = {
    ".git", "__pycache__", "node_modules", ".venv", "venv", ".mypy_cache",
    ".pytest_cache", "dist", "build", ".idea", ".vscode",
}
# 커밋되지 않는 설정 파일. 여기엔 실제 값이 들어 있는 것이 정상이다
SKIP_NAME_EXACT = {"harness.yaml", "harness.yml", "harness.json"}
MAX_BYTES = 2_000_000


# ── 규칙 ────────────────────────────────────────────────────────────────────
# (이름, 정규식, 치환 제안). 정규식에 실제 비밀값을 적지 않는다 — 형태만 적는다.
RULES: list[tuple[str, str, str]] = [
    # 1 개인 식별자 — 구체 목록은 scan.local.json 에서 읽는다 (아래 load_local)
    #   공개 저장소에 금지어 목록을 적으면 그 목록 자체가 노출이다.

    # 2 환경 고유값
    ("개인 홈 경로", r"/Users/[A-Za-z0-9._-]+|C:\\\\Users\\\\[A-Za-z0-9._-]+",  # scan:allow
     "$HOME 또는 설정값으로"),
    # 계정이 실제로 박힌 것만 잡는다. `GoogleDrive-*/` 나 `GoogleDrive-<계정>` 은
    # 와일드카드·자리표시자이므로 오탐이다 — 거짓 경보가 반복되면 가드가 무시된다.
    ("클라우드 마운트 경로", r"CloudStorage/GoogleDrive-[A-Za-z0-9._%+-]+@",  # scan:allow
     "설정값 mount_root 로"),
    ("드라이브·채널 ID", r"\b0A[A-Za-z0-9_-]{15,}\b",
     "설정값으로: config 의 drive_id"),

    # 3 비밀값
    ("API 키 접두", r"\bsk-[A-Za-z0-9]{16,}|\bghp_[A-Za-z0-9]{16,}|\bgsk_[A-Za-z0-9]{16,}|\bxox[bpas]-[A-Za-z0-9-]{16,}",
     "즉시 폐기하고 재발급한다. 저장소에 넣지 않는다"),
    ("PEM 블록", r"-----BEGIN [A-Z ]*PRIVATE KEY-----",
     "키 파일은 저장소 밖(~/.config)에 둔다"),
    ("토큰 리터럴", r"(?i)\b(secret|token|api[_-]?key|password)\b\s*[:=]\s*[\"'][^\"'<>{}$\s]{12,}[\"']",
     "환경변수나 설정 파일 참조로 바꾼다"),

    # 4 폐지된 레이아웃 — 패키징 이전 형제-프로젝트 경로.
    #   이 경로는 새로 설치한 사람의 기계에 존재하지 않는다. 임포트는 통과하고
    #   실행 시점에만 터지므로, 올리기 전에 여기서 잡는다.
    ("폐지된 경로", r"projects/(nworks-agent|rpbd-infra|fathom-agent|[a-z-]*weekly-dashboard)"
                r"|/scripts/(nworks|agents)/",
     "저장소 안 경로나 설정의 작업공간(paths.*)으로"),

    # 5 개인 환경 도구 — 특정 개인의 작업 도구가 실행 경로에 들어가면
    #   받은 사람은 없는 도구를 찾다가 막힌다. `참고/` 안에서는 허용한다(설명 대상이므로).
    # 도구 이름을 그대로 적으면 이 줄 자신이 규칙에 걸린다 — 쪼개서 붙인다
    ("개인 환경 도구",
     r"(?i)(?<![a-z])(" + "|".join(["c" + "mux", "claude" + "x", "agy" + "x",
                                    "codex" + "x", "bea" + "xx", "ura" + "xx"]) + r")(?![a-z])",
     "설정 훅으로 빼거나 설명 자료 폴더로 옮긴다"),

    # 6 연락처
    # 서비스 제공자의 시스템 발신 주소는 조직 고유값이 아니다 — 판정 로직에 필요하므로 허용한다
    ("이메일", r"[A-Za-z0-9._%+-]+@(?!example\.(org|com)\b|(?:[A-Za-z0-9.-]+\.)?"
               r"(?:navercorp|worksmobile|google|atlassian|slack|github|anthropic|openai)\.com\b)"
               r"[A-Za-z0-9.-]+\.[A-Za-z]{2,}",
     "example.org 또는 자리표시자로"),
    ("전화번호", r"\b01[016-9][-.\s]?\d{3,4}[-.\s]?\d{4}\b",
     "지운다"),
]
LOCAL_RULES_FILE = "scan.local.json"


def load_local(root: Path) -> list[tuple[str, str, str]]:
    """조직 고유 금지어를 로컬 파일에서 읽는다 — 저장소에 커밋하지 않는다.

    {"names": ["홍길동"], "accounts": ["hong"], "orgs": ["우리회사", "ourcorp.co.kr"]}

    이 파일이 없으면 일반 패턴(이메일·키 접두·경로)만으로 스캔하고 그 사실을 알린다.
    실제 이름과 계정은 조직마다 다르므로 이 저장소가 알 수 없고, 알아서도 안 된다.
    """
    p = root / LOCAL_RULES_FILE
    if not p.is_file():
        return []
    try:
        d = json.loads(p.read_text(encoding="utf-8"))
    except Exception:                                       # noqa: BLE001
        return []
    out = []
    for key, label, hint in (
        ("names",    "사람 이름",   "역할로 바꾼다: 운영자 · 구성원 A"),
        ("accounts", "개인 계정",   "설정값으로 뺀다"),
        ("orgs",     "조직 고유명", "일반명 또는 설정값으로"),
    ):
        vals = [re.escape(str(v)) for v in (d.get(key) or []) if str(v).strip()]
        if vals:
            out.append((label, r"(?:" + "|".join(vals) + r")", hint))
    return out


COMPILED = [(name, re.compile(pat), hint) for name, pat, hint in RULES]

# 자리표시자·문서 예시는 위반이 아니다.
#
# 줄 전체를 면제하면 안 된다. plist 는 모든 줄이 `<string>` 으로 감싸여 있어
# 태그 하나가 그 줄의 실제 값을 통째로 가렸고, `"<계정>{{ORG_DOMAIN}}"` 처럼
# 토큰과 실제 값이 한 줄에 섞이면 토큰이 값을 가렸다. 그래서 **지우고 남은 것을 본다.**
ALLOW_MASK = re.compile(
    r"example\.(org|com)"              # 문서용 도메인
    r"|\{\{[A-Z_]+\}\}"                # 치환 토큰
    r"|<[가-힣A-Za-z_][가-힣A-Za-z0-9_ -]*>"  # <계정> · <이름> 꼴 자리표시자
    r"|YOUR_[A-Z_]*|__[A-Z_]+__|\bxxx+\b",
    re.IGNORECASE,
)
# 이것만 줄 전체를 면제한다 — 규칙 정의처럼 글쓴이가 명시적으로 표시한 줄
ALLOW_LINE = re.compile(r"scan:allow")
# XML·HTML 태그는 자리표시자가 아니다. 껍데기만 지우고 안의 값은 검사한다
XML_TAG = re.compile(r"</?[A-Za-z][A-Za-z0-9:_-]*\s*/?>")


SKIP_FILE = {"scan.local.json"}
REFERENCE_DIR = "참고"       # 설치 대상이 아닌 설명 자료
SKIPPED: list[Path] = []   # 검사하지 않은 파일 — 끝에 반드시 보고한다          # 금지어 목록 자신 — .gitignore 대상


def _git_tracked(root: Path) -> set[Path] | None:
    """git 저장소라면 **커밋될 파일 목록**을 돌려준다.

    .gitignore 로 제외되는 것(생성물·실제 설정)은 공개되지 않으므로 검사 대상이 아니다.
    대상을 정확히 좁혀야 거짓 경보가 없고, 거짓 경보가 없어야 이 게이트가 산다.
    """
    import subprocess
    if not (root / ".git").is_dir():
        return None
    try:
        out = subprocess.run(
            ["git", "-C", str(root), "ls-files", "--cached", "--others",
             "--exclude-standard"],
            capture_output=True, text=True, timeout=60)
        if out.returncode != 0:
            return None
        return {root / line for line in out.stdout.splitlines() if line.strip()}
    except Exception:                                      # noqa: BLE001
        return None


BINARY_SUFFIX = {
    ".m4a", ".mp3", ".wav", ".mp4", ".mov", ".png", ".jpg", ".jpeg", ".gif",
    ".pdf", ".zip", ".gz", ".whl", ".woff", ".woff2", ".ico", ".webp",
}


def is_text(p: Path) -> bool:
    """확장자만 믿지 않는다.

    `nwctl` 처럼 확장자 없는 실행 스크립트가 이 판정을 빠져나가면
    게이트는 0건을 보고하면서 그 파일을 보지 않는다 — 가장 비싼 실패다.
    그래서 앞부분을 직접 읽어 텍스트인지 본다.
    """
    if p.suffix.lower() in TEXT_SUFFIX:
        return True
    if p.suffix.lower() in BINARY_SUFFIX:
        return False
    if p.name in {"Dockerfile", "Makefile"}:
        return True
    try:
        head = p.open("rb").read(4096)
    except OSError:
        return False
    if not head:
        return False
    if b"\0" in head:
        return False
    try:
        head.decode("utf-8")
    except UnicodeDecodeError:
        return False
    return True


def walk(root: Path):
    tracked = _git_tracked(root)
    for p in sorted(root.rglob("*")):
        if tracked is not None and p.is_file() and p not in tracked:
            continue
        if any(part in SKIP_DIR for part in p.parts):
            continue
        if p.name in SKIP_FILE or p.name in SKIP_NAME_EXACT or ".local." in p.name:
            continue
        # `참고/` 는 설치 대상이 아니라 설명 자료다 — 거기 적힌 도구 이름은 위반이 아니다
        if REFERENCE_DIR in p.parts:
            continue
        if not p.is_file():
            continue
        if not is_text(p):
            SKIPPED.append(p)
            continue
        try:
            if p.stat().st_size > MAX_BYTES:
                continue
        except OSError:
            continue
        yield p


def scan_file(path: Path, root: Path) -> list[dict]:
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError as e:
        return [{"file": str(path.relative_to(root)), "line": 0,
                 "rule": "읽기 실패", "hint": str(e)[:80]}]
    out = []
    for i, line in enumerate(text.splitlines(), 1):
        if ALLOW_LINE.search(line):
            continue
        # 허용 대상만 지운다. 지우고 남은 자리에 실제 값이 있으면 그것이 위반이다
        probe = XML_TAG.sub(" ", ALLOW_MASK.sub(" ", line))
        for name, rx, hint in COMPILED:
            if rx.search(probe):
                # 값은 담지 않는다 — 위치와 규칙만
                out.append({"file": str(path.relative_to(root)), "line": i,
                            "rule": name, "hint": hint})
                break
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("root", nargs="?", default=".", help="스캔할 폴더 (기본: 현재)")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--fix-hint", action="store_true", help="치환 제안을 함께 낸다")
    a = ap.parse_args()

    root = Path(a.root).resolve()
    if not root.is_dir():
        print(f"폴더가 아니다: {root}", file=sys.stderr)
        return 2

    local = load_local(root)
    if local:
        COMPILED.extend((n, re.compile(p), h) for n, p, h in local)
    elif not a.json:
        print(f"! {LOCAL_RULES_FILE} 없음 — 조직 고유 금지어 없이 일반 패턴만 검사한다\n"
              f"  이름·계정·조직명을 넣으려면 그 파일을 만든다 (커밋하지 않는다)\n")

    findings, files = [], 0
    for p in walk(root):
        files += 1
        findings += scan_file(p, root)

    if a.json:
        print(json.dumps({"root": str(root), "files": files,
                          "violations": len(findings), "findings": findings,
                          "skipped": [str(s.relative_to(root)) for s in SKIPPED]},
                         ensure_ascii=False, indent=1))
        return 1 if findings else 0

    print(f"스캔 {files}개 파일 · 위반 {len(findings)}건")
    if SKIPPED:
        print(f"  검사 안 함 {len(SKIPPED)}개 (바이너리·빈 파일) — 내용 확인 불가:")
        for s in SKIPPED[:6]:
            print(f"    {s.relative_to(root)}")
        if len(SKIPPED) > 6:
            print(f"    … 외 {len(SKIPPED) - 6}개")
    if not findings:
        print("✓ 깨끗하다 — 공개해도 된다")
        return 0

    by_rule: dict[str, list[dict]] = {}
    for f in findings:
        by_rule.setdefault(f["rule"], []).append(f)

    print()
    for rule, items in sorted(by_rule.items(), key=lambda kv: -len(kv[1])):
        print(f"  [{len(items):>3}] {rule}")
        for it in items[:5]:
            print(f"        {it['file']}:{it['line']}")
        if len(items) > 5:
            print(f"        … 외 {len(items) - 5}곳")
        if a.fix_hint:
            print(f"        → {items[0]['hint']}")
    print(f"\n✗ 위반 {len(findings)}건 — 공개하면 안 된다")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
