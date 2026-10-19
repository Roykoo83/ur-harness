#!/usr/bin/env python3
"""산출물 U 반영 후보표를 만든다 (파이프라인 ①~④). 쓰기는 하지 않는다.

    python3 scripts/deliverables/scan.py [--dry] [--force]

평일 17:00 launchd(`com.{{ORG_NAME}}.deliverables-scan`). 주말·공휴일이면 조용히 끝낸다.

왜 자동 복사를 하지 않나: 발주처에 나간 문서를 기계가 골라 회사 드라이브에 올리는 구조는
위험이 이득보다 크다. **md5가 U에 없다는 사실은 '올려야 한다'가 아니라 '다른 버전일 수 있다'** 이다
(2026-09-04 실측: NIPA 29건이 전부 작업 중간본이었고 최종본은 이미 U에 있었다).
그래서 여기서는 후보표만 만들고, 복사는 사람이 체크한 것만 apply.py 가 한다.
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import re
import subprocess
import sys
import unicodedata
from pathlib import Path

PROJ = Path(__file__).resolve().parents[2]
REPO = PROJ.parents[1]
CFG = json.loads((Path(__file__).parent / "config.json").read_text(encoding="utf-8"))
DATA = PROJ / "data" / "deliverables"
OUT = PROJ / "output" / "deliverables"
DECIDED = DATA / "decided.jsonl"
NW = REPO / "projects" / "nworks-agent"

EXT = set(CFG["확장자"])
U_DRIVE, SSOT_DRIVE = CFG["drives"]["u"], CFG["drives"]["ssot"]



def _send_kwargs() -> dict:
    """수신자 인자 — 설정의 operator.account 로 조직도를 조회해 정한다.

    대상을 못 찾으면 빈 dict 를 돌려주고, 부르는 쪽이 보내지 않는다.
    사람 ID 를 설정 파일에 적어 두면 담당자가 바뀔 때마다 따라다녀야 한다.
    """
    import sys as _s
    from pathlib import Path as _P
    _s.path.insert(0, str(_P(__file__).resolve().parents[2] / "core"))
    from lib.identity import alert_target                        # noqa: PLC0415
    tgt = alert_target()
    if not tgt:
        return {}
    return {"user_id": tgt[1]} if tgt[0] == "user" else {"channel_id": tgt[1]}


def nfc(s: str) -> str:
    return unicodedata.normalize("NFC", s)


def log(msg: str) -> None:
    print(f"[{dt.datetime.now():%H:%M:%S}] {msg}", flush=True)


def is_business_day(d: dt.date) -> bool:
    try:
        import holidays
        return d.weekday() < 5 and d not in holidays.KR(years=range(d.year, d.year + 2))
    except Exception:
        return d.weekday() < 5


# ── ① 수집 ────────────────────────────────────────────────────────────────
def lsjson(drive: str) -> list[dict]:
    """rclone lsjson 은 전체를 메모리에 모았다가 마지막에 쓴다. 진행 중 파일은 읽지 않는다."""
    r = subprocess.run(
        ["rclone", "lsjson", "--recursive", "--files-only", "--hash",
         "--drive-team-drive", drive, "gdrive:"],
        capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError(f"lsjson 실패({drive}): {(r.stderr or '')[-200:]}")
    return json.loads(r.stdout)


def local_sources() -> list[tuple[Path, str]]:
    out = []
    for spec in CFG["로컬원천"]:
        pat = spec["path"].replace("~", str(Path.home()))
        if "*" in pat:
            base, _, tail = pat.partition("*")
            for d in sorted(Path(base).glob("*" + tail)):
                if d.is_dir():
                    out.append((d, spec["label"]))
        elif Path(pat).is_dir():
            out.append((Path(pat), spec["label"]))
    return out


def excluded(p: str) -> bool:
    return any(x in p for x in CFG["로컬제외"])


def collect_local() -> list[dict]:
    rows = []
    for root, label in local_sources():
        for f in root.rglob("*"):
            if not f.is_file() or f.suffix.lower() not in EXT:
                continue
            s = str(f)
            if excluded(s) or f.name.startswith("~$"):
                continue
            try:
                if f.stat().st_size > 300 * 1024 * 1024:
                    continue
                h = hashlib.md5(f.read_bytes()).hexdigest()
            except OSError:
                continue
            rows.append({"origin": "local", "label": label, "src": s,
                         "name": nfc(f.name), "size": f.stat().st_size, "md5": h,
                         "mtime": f.stat().st_mtime})
    return rows


def collect_ssot(ssot: list[dict]) -> list[dict]:
    pre = CFG["ssot원천"]
    rows = []
    for f in ssot:
        p = nfc(f["Path"])
        if not p.startswith(pre) or Path(p).suffix.lower() not in EXT:
            continue
        rows.append({"origin": "ssot", "label": "SSOT", "src": p,
                     "name": nfc(f["Name"]), "size": f["Size"],
                     "md5": (f.get("Hashes") or {}).get("md5"), "mtime": None})
    return rows


# ── ③ 버전판정 ────────────────────────────────────────────────────────────
VER = re.compile(r"(_?v?\d+(\.\d+)*)|(\d{6,8})|(최종|초안|검수용|수정본|제출본|인쇄용)", re.I)


def stem_key(name: str) -> str:
    """버전 토큰과 날짜를 지운 이름. 같은 문서의 다른 버전을 묶는 데 쓴다."""
    s = nfc(Path(name).stem)
    s = VER.sub("", s)
    return re.sub(r"[\s_\-().]+", "", s).lower()


def judge(row: dict, u_by_name: dict, u_by_key: dict) -> tuple[str, list[str]]:
    same = u_by_name.get(row["name"])
    if same:
        return "버전충돌", same[:3]
    near = u_by_key.get(stem_key(row["name"]))
    if near:
        return "유사버전", near[:3]
    return "신규", []


# ── ④ 매핑 ────────────────────────────────────────────────────────────────
def map_dst(row: dict) -> tuple[str | None, str]:
    for m in CFG["과제매핑"]:
        hit = (row["origin"] == "ssot" and nfc(row["src"]).startswith(CFG["ssot원천"] + m["key"] + "/")) \
              or (row["origin"] == "local" and m.get("local") and f"/projects/{m['local']}/" in row["src"])
        if not hit:
            continue
        sub = ""
        for pat, s in m.get("hints", []):
            if re.search(pat, row["name"], re.I) or re.search(pat, nfc(row["src"]), re.I):
                sub = s
                break
        base = m["u"] + ("/" + sub if sub else "")
        return f"{base}/{row['name']}", ("제안" if sub else "과제루트")
    return None, "미정"


def load_decided() -> set[str]:
    if not DECIDED.exists():
        return set()
    return {json.loads(x)["md5"] for x in DECIDED.read_text(encoding="utf-8").splitlines() if x.strip()}


def send(text: str) -> bool:
    sys.path.insert(0, str(NW / "scripts"))
    try:
        from nworks.client import NworksClient
        from nworks.config import load_config
        from nworks.domains import bot_send_text
        bot_send_text(NworksClient(), load_config()["bot_id"], text,
                      **_send_kwargs())
        return True
    except Exception as e:
        log(f"알림 발송 실패: {str(e)[:160]}")
        return False


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry", action="store_true", help="후보표를 쓰지 않고 요약만")
    ap.add_argument("--force", action="store_true", help="휴일에도 실행")
    a = ap.parse_args()

    today = dt.date.today()
    if not a.force and not is_business_day(today):
        log("휴일 - 종료")
        return 0

    DATA.mkdir(parents=True, exist_ok=True)
    OUT.mkdir(parents=True, exist_ok=True)

    log("U 열거")
    u = lsjson(U_DRIVE)
    log("SSOT 열거")
    ssot = lsjson(SSOT_DRIVE)

    umd5 = {(f.get("Hashes") or {}).get("md5") for f in u}
    u_by_name: dict[str, list[str]] = {}
    u_by_key: dict[str, list[str]] = {}
    for f in u:
        p = nfc(f["Path"])
        u_by_name.setdefault(nfc(f["Name"]), []).append(p)
        u_by_key.setdefault(stem_key(f["Name"]), []).append(p)

    rows = collect_local() + collect_ssot(ssot)
    decided = load_decided()
    cand = [r for r in rows if r["md5"] and r["md5"] not in umd5 and r["md5"] not in decided]
    # 같은 내용이 여러 원천에 있으면 하나만 (로컬 우선 = 최신 제작본)
    seen, uniq = set(), []
    for r in sorted(cand, key=lambda x: 0 if x["origin"] == "local" else 1):
        if r["md5"] in seen:
            continue
        seen.add(r["md5"])
        uniq.append(r)

    for r in uniq:
        r["판정"], r["근거"] = judge(r, u_by_name, u_by_key)
        r["dst"], r["매핑"] = map_dst(r)

    log(f"후보 {len(uniq)}건 (원천 {len(rows)} · U중복제외 · 처리이력제외 {len(decided)})")
    if a.dry:
        for r in uniq[:40]:
            print(f"  [{r['판정']}/{r['매핑']}] {r['name']}  >> {r['dst']}")
        return 0

    path = OUT / f"후보표_{today:%Y-%m-%d}.md"
    lines = [
        f"# 산출물 U 반영 후보표 ({today:%Y-%m-%d})", "",
        "> 자동 생성. **복사는 여기서 체크한 것만** `apply.py` 가 수행한다.",
        "> `- [ ]` 를 `- [x]` 로 바꾸면 복사, `- [-]` 로 바꾸면 앞으로 다시 올리지 않는다.",
        "> 목적지가 틀리면 `>>` 뒤 경로를 직접 고친다. **판정이 `버전충돌`·`유사버전`이면 반드시 눈으로 확인한다.**", "",
        f"후보 {len(uniq)}건", "",
    ]
    order = {"버전충돌": 0, "유사버전": 1, "신규": 2}
    for r in sorted(uniq, key=lambda x: (order[x["판정"]], x["매핑"] == "미정", x["dst"] or "")):
        flag = {"버전충돌": "🔴 버전충돌", "유사버전": "🟡 유사버전", "신규": "🟢 신규"}[r["판정"]]
        mark = "" if r["매핑"] != "미정" else "  ⚠️ 목적지 미정"
        lines.append(f"- [ ] `{r['md5'][:8]}` {flag}{mark} · {r['size']//1024:,}KB · {r['label']}")
        lines.append(f"      원천: `{r['src']}`")
        lines.append(f"      >> `{r['dst'] or '(정하세요)'}`")
        for e in r["근거"]:
            lines.append(f"      U 기존: `{e}`")
        lines.append("")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    log(f"후보표: {path}")

    new_conf = sum(1 for r in uniq if r["판정"] != "신규")
    if uniq:
        send(f"[산출물 U반영] 후보 {len(uniq)}건 (확인필요 {new_conf}건)\n{path.name}\n"
             f"체크 후 apply.py 실행")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
