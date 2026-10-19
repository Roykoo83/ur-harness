"""A2 mail parser — 00_raw 메일 JSON을 10_stage 정규화 md로 변환.

Stage 원칙: 재생성 가능 산출물 — 실행할 때마다 전체 재생성한다 (멱등, 상태 없음).
각 md는 frontmatter에 raw 역링크(raw_path)를 반드시 포함한다 (SSOT 규약).

Input : output/raw_mail/<계정키>/<YYYY-MM>/<mailId>.json   (수집 계정은 {{BOT_USER}} 하나)
Output: output/stage/mail_<계정키>/<YYYY-MM>/<mailId>_<slug>.md
        output/stage/mail_<계정키>/index.md  (수신일 역순 전체 목록)

계정 키 없는 `mail/` 경로는 쓰지 않는다 — {{OPERATOR_USER}} 수집 시절의 이름이고 2026-08-12 에 멈췄다.
"""
import json
import re
import sys
from html.parser import HTMLParser
from pathlib import Path

# 민감도 분류기 — rpbd 공용 모듈 재사용 (통합지시 2026-08-12 §4).
# 태깅 전용: 운영자 방침(메일은 기밀 아님)에 따라 수집·파싱은 막지 않고,
# SSOT 승격·대시보드 노출 필터가 쓸 표식만 남긴다.
sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "rpbd-infra" / "src"))
try:
    from rpbd.ssot.sensitivity import classify as classify_sensitivity
except ImportError:  # rpbd-infra 부재 시에도 파서는 동작해야 한다
    def classify_sensitivity(name, category=None, rel_path=None):
        return "normal"


class _Text(HTMLParser):
    SKIP = {"style", "script", "head"}
    BLOCK = {"p", "div", "br", "tr", "li", "table", "h1", "h2", "h3", "h4"}

    def __init__(self):
        super().__init__()
        self.out = []
        self._skip = 0

    def handle_starttag(self, tag, attrs):
        if tag in self.SKIP:
            self._skip += 1
        elif tag in self.BLOCK:
            self.out.append("\n")

    def handle_endtag(self, tag):
        if tag in self.SKIP and self._skip:
            self._skip -= 1

    def handle_data(self, data):
        if not self._skip:
            self.out.append(data)


def html_to_text(html: str) -> str:
    p = _Text()
    p.feed(html or "")
    text = "".join(p.out)
    text = re.sub(r"[ \t ]+", " ", text)
    text = re.sub(r"\n\s*\n+", "\n\n", text)
    return text.strip()


def _slug(subject: str, limit: int = 40) -> str:
    s = re.sub(r"^((RE|FW|FWD|답장)\s*:\s*)+", "", subject or "", flags=re.I)
    s = re.sub(r'[/\\:*?"<>|\s]+', "_", s).strip("_")
    return s[:limit] or "무제"


def _addr_list(rs) -> str:
    return ", ".join(
        f"{r.get('name') or ''}<{r.get('email')}>".strip() for r in (rs or []))


def parse_mail(raw_json: Path, stage_root: Path) -> dict:
    m = json.loads(raw_json.read_text())
    mo = m.get("mail") or m
    atts = [a for a in (m.get("attachments") or [])
            if not a.get("cid") and a.get("contentDisposition") != "inline"]
    mid = raw_json.stem
    month = raw_json.parent.name
    received = mo.get("receivedTime", "")
    subject = mo.get("subject") or "(제목 없음)"
    body = html_to_text(mo.get("body") or "")

    att_dir = raw_json.parent / f"{mid}_att"
    att_lines = []
    sens_max = classify_sensitivity(subject)
    for a in atts:
        f = a.get("filename", "?")
        saved = (att_dir / re.sub(r'[/\\:*?"<>|\x00-\x1f]', "_", f)[:200]).exists()
        sens = classify_sensitivity(f)
        tag = "" if sens == "normal" else f" [민감도:{sens}]"
        att_lines.append(f"- {f} ({a.get('size', 0):,}B){'' if saved else ' [미저장]'}{tag}")
        if (sens == "high") or (sens == "medium" and sens_max == "normal"):
            sens_max = sens

    out_dir = stage_root / month
    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / f"{mid}_{_slug(subject)}.md"
    fm = [
        "---",
        f"mail_id: {mid}",
        f"subject: \"{subject.replace(chr(34), chr(39))}\"",
        f"from: \"{_addr_list([mo.get('from')] if mo.get('from') else [])}\"",
        f"to: \"{_addr_list(mo.get('to'))}\"",
        f"cc: \"{_addr_list(mo.get('cc'))}\"",
        f"received: {received}",
        f"attachments: {len(atts)}",
        f"sensitivity: {sens_max}",
        # 계정별 폴더를 따라간다. 하드코딩된 `00_raw/mail/` 은 {{OPERATOR_USER}} 수집 시절의 이름이고
        # 그 폴더는 2026-08-12 에 멈췄다 — 그 뒤 승격된 메일 1,132건이 전부 죽은 경로를
        # 가리키고 있었다 (2026-09-28 발견). stage_root.name 이 곧 raw 폴더명이다.
        f"raw_path: 00_raw/{stage_root.name}/{month}/{mid}.json",
        "---",
    ]
    sections = ["\n".join(fm), f"# {subject}", body or "(본문 없음)"]
    if att_lines:
        sections.append("## 첨부\n" + "\n".join(att_lines))
    out.write_text("\n\n".join(sections))
    return {"mid": mid, "month": month, "received": received,
            "subject": subject, "file": f"{month}/{out.name}",
            "from": (mo.get("from") or {}).get("email", ""), "atts": len(atts)}


def run(raw_root: Path, stage_root: Path) -> dict:
    if stage_root.exists():  # full regenerate — stage is disposable by contract
        for old in stage_root.rglob("*.md"):
            old.unlink()
    rows = []
    for f in sorted(raw_root.glob("*/[0-9]*.json")):
        rows.append(parse_mail(f, stage_root))
    rows.sort(key=lambda r: r["received"], reverse=True)
    # 색인 제목은 계정키에서 만든다. `research@` 가 하드코딩돼 있어 SSOT 정본
    # `rbp-index.md` 첫 줄에 폐지된 이름이 매시간 새로 쓰이고 있었다 (2026-10-01 교정).
    acct = stage_root.name.removeprefix("mail_") or stage_root.name
    span = f"{rows[-1]['received'][:10]} ~ {rows[0]['received'][:10]}" if rows else "비어 있음"
    idx = [f"# {acct} 메일 색인 (stage — 자동 재생성)",
           f"\n총 {len(rows)}건 · 수신 {span} · 파서: nworks-agent A2\n",
           "| 수신일 | 제목 | 발신 | 첨부 | 파일 |", "|---|---|---|---|---|"]
    for r in rows:
        day = (r["received"] or "")[:10]
        subj = r["subject"].replace("|", "\\|")[:60]
        # 링크를 <> 로 감싼다 — 제목의 닫는 괄호가 마크다운 링크를 끊는다 (2026-10-01)
        idx.append(f"| {day} | {subj} | {r['from']} | {r['atts'] or ''} | [{r['mid']}](<{r['file']}>) |")
    stage_root.mkdir(parents=True, exist_ok=True)
    (stage_root / "index.md").write_text("\n".join(idx) + "\n")
    return {"parsed": len(rows)}


if __name__ == "__main__":
    root = Path(__file__).resolve().parents[2]
    # argv[1]: raw_mail 하위 계정 키. 계정별로 stage/mail_<키>로 분리한다.
    # 2026-09-05: research(={{OPERATOR_USER}}) 특례 경로 제거 — 수집은 {{BOT_USER}} 단일이다.
    key = sys.argv[1] if len(sys.argv) > 1 else "{{BOT_USER}}"
    raw = root / "output" / "raw_mail" / key
    stage = root / "output" / "stage" / f"mail_{key}"
    print(json.dumps(run(raw, stage), ensure_ascii=False))
