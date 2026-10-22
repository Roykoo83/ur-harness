#!/usr/bin/env python3
"""Build INDEX.md for a project from the parse manifests.

Usage: make-index.py <project-dir>

Reads <project-dir>/parsed/<track>/_manifest.json for each track (archive, work)
and emits a browsable table: original file -> parsed markdown, with a snippet
pulled from the parsed content so the index is skimmable without opening files.
"""
import json
import re
import sys
from pathlib import Path

STATUS_LABEL = {
    "ok": "✅",
    "scan": "🖼 스캔",     # image-only PDF, no text layer
    "empty": "⚠ 빈문서",
    "error": "❌ 실패",
    "skip": "— 대상아님",
}

TRACKS = [
    ("source/archive", "source/archive/", "과거 협약·계약·행정·정산 자료 (마이그레이션)"),
    ("source/work", "source/work/", "과업 수행 원본 자료"),
]

# Folders holding personal data (payslips, bank details, insurance certificates).
# Listed as counts only — individual filenames carry employee names, and this
# index is committed to git while the folders themselves are not.
REDACT_PREFIXES = ("03_증빙", "02_사업비/선금신청")


def snippet(md_path, limit=70):
    """First substantive line of parsed content, for the index preview column."""
    try:
        text = md_path.read_text(encoding="utf-8")
    except OSError:
        return ""
    body = text.split("\n---\n", 1)[-1]
    for line in body.splitlines():
        line = line.strip()
        if not line or line.startswith(("|", "#", ">", "-")):
            continue
        line = re.sub(r"[*_`]", "", line)
        if line.startswith("_("):        # parser notes, not content
            return line.strip("_()")
        return line[:limit] + ("…" if len(line) > limit else "")
    for line in body.splitlines():       # table-only documents
        if line.strip().startswith("|"):
            cells = [c.strip() for c in line.strip("|").split("|") if c.strip()]
            if cells:
                joined = " / ".join(cells)
                return joined[:limit] + ("…" if len(joined) > limit else "")
    return ""


def human(n):
    return f"{n/1_048_576:.1f}MB" if n >= 1_048_576 else f"{n/1024:.0f}KB"


def main():
    # 인자가 없으면 설정의 정본 경로를 쓴다 — 스케줄러는 인자 없이 부른다.
    # 예전에는 여기서 IndexError 로 죽었고, 로그를 봐야만 이유를 알 수 있었다
    if len(sys.argv) > 1:
        root = Path(sys.argv[1]).resolve()
    else:
        import sys as _s
        _s.path.insert(0, str(Path(__file__).resolve().parent))
        from lib.config import cfg, paths                     # noqa: PLC0415
        mount, drive = paths.mount_root, cfg("org.drives.ssot.name")
        if not (mount and drive):
            raise SystemExit(
                "정본 경로를 알 수 없다.\n"
                "  인자로 주거나, 설정에 operator.mount_root 와 "
                "org.drives.ssot.name 을 채운다")
        root = (Path(mount) / "공유 드라이브" / drive).resolve()
    if not root.is_dir():
        raise SystemExit(f"정본 폴더가 없다: {root}")
    out = [f"# 자료 인덱스 — {root.name}", "",
           "원본은 `source/`(읽기 전용)에, 파싱본(Markdown)은 `parsed/source/`에 같은 구조로 있습니다.",
           "에이전트는 **파싱본을 읽고**, 원문 확인이 필요할 때만 원본을 엽니다.",
           "편집이 필요하면 원본을 고치지 말고 `workspace/`로 복사해 작업합니다 "
           "(`scripts/pull-source.sh`).", ""]

    grand = 0
    tally_all = {}
    for track, prefix, desc in TRACKS:
        man_path = root / "parsed" / track / "_manifest.json"
        if not man_path.exists():
            continue
        manifest = json.loads(man_path.read_text(encoding="utf-8"))
        grand += len(manifest)

        out += [f"## {prefix} — {desc}", "", f"파일 {len(manifest)}개", ""]

        groups = {}
        for m in manifest:
            groups.setdefault(str(Path(m["src"]).parent), []).append(m)

        for folder in sorted(groups):
            items = sorted(groups[folder], key=lambda m: m["src"])
            label = folder if folder != "." else "(최상위)"

            if folder.startswith(REDACT_PREFIXES):
                for m in items:
                    tally_all[m["status"]] = tally_all.get(m["status"], 0) + 1
                kinds = ", ".join(sorted({m["ext"].lstrip(".") for m in items}))
                dates = sorted(m["mtime"] for m in items)
                span = dates[0] if dates[0] == dates[-1] else f"{dates[0]} ~ {dates[-1]}"
                out += [f"### `{prefix}{label}` 🔒", "",
                        f"개인정보 포함 폴더 — 파일 **{len(items)}건** ({kinds}), {span}. "
                        "목록·내용은 인덱스에 싣지 않습니다 (RULES.md 참조).",
                        "원본은 로컬 `" + prefix + label + "/`, 파싱본은 "
                        f"`parsed/{track}/{label}/` 에 있으며 git에는 커밋되지 않습니다.", ""]
                continue

            out += [f"### `{prefix}{label}`", "",
                    "| 파일 | 형식 | 수정일 | 상태 | 내용 |",
                    "|---|---|---|---|---|"]
            for m in items:
                name = Path(m["src"]).name
                link = f"parsed/{track}/{m['parsed']}".replace(" ", "%20")
                status = STATUS_LABEL.get(m["status"], m["status"])
                tally_all[m["status"]] = tally_all.get(m["status"], 0) + 1
                text = snippet(root / "parsed" / track / m["parsed"]).replace("|", "\\|")
                out.append(f"| [{name}]({link}) | {m['ext'].lstrip('.')} "
                           f"| {m['mtime']} | {status} | {text} |")
            out.append("")

    out += ["## 파싱 집계", "",
            "| 상태 | 개수 | 의미 |", "|---|---|---|"]
    meaning = {
        "ok": "본문·표 정상 추출",
        "scan": "텍스트 레이어 없는 스캔 PDF — 원본 열람 필요",
        "empty": "추출 결과 없음",
        "error": "파싱 실패",
        "skip": "이미지 등 텍스트 없는 형식",
    }
    for k in ["ok", "scan", "empty", "error", "skip"]:
        if k in tally_all:
            out.append(f"| {STATUS_LABEL[k]} | {tally_all[k]} | {meaning[k]} |")
    out += ["", f"총 {grand}개 파일.", "",
            "재생성: `python3 scripts/parse-docs.py <archive|work> parsed/<track>` "
            "→ `python3 scripts/make-index.py <프로젝트경로>`", ""]

    (root / "INDEX.md").write_text("\n".join(out), encoding="utf-8")
    print(f"INDEX.md 생성 — {grand}개 파일, " +
          ", ".join(f"{k}={v}" for k, v in sorted(tally_all.items())))


if __name__ == "__main__":
    main()
