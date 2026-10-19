#!/usr/bin/env python3
"""A4 메일 큐레이션 — 전 계열 원본에 판정을 얹어 **정리대장** 하나를 만든다.

운영자 기준 (2026-09-30 · 10-01):
  · {{BOT_USER}}@ 에 수집되는 **모든 메일이 원본**이다. 원본은 지우지 않는다
  · 6월부터의 기존({{OPERATOR_USER}}) 파싱분도 **살려서 U 에 둔다** — 계열이 둘이어도 코퍼스는 하나
  · **SSOT 로 넘어가는 것은 업무에 필요한 것만** — 업무와 무관한 메일은 걸러서 보낸다
  · 필터 조건은 **지금 조건을 유지한다** → 규칙은 `config/brief_filter.json` 이 계속 소유하고,
    이 스크립트는 그것을 `daily_brief` 의 해석 함수로 **그대로 적용**만 한다 (중복 구현 금지)

그래서 하는 일은 판정뿐이다. 메일을 지우거나 옮기지 않는다.

계열 (U `5. Naverworks/00_raw/mail_<계정키>/`)
  {{BOT_USER}}  live    received >= T0   매시간 증가 — 원본의 정의
  {{OPERATOR_USER}}    frozen  received <  T0   563건 고정. 늘거나 줄면 이상
  T0 = 2026-08-12T15:29:53+09:00 — 두 계열이 이어지는 **시각** (실측: {{OPERATOR_USER}}#778 = rbp#2)

식별자 3층
  L1 mailId                    편지함 내에서만 유일 — 단독 사용 금지 (계정 간 496개 충돌)
  L2 물리키 <접두><mailId>      파일명·SSOT 경로 (rbp-2 · 운영자-778)
  L3 논리키 uid                같은 발송 사건. sha1(sentTime|from|subject정규화)
     size 를 키에 넣지 않는 이유: 같은 메일도 편지함마다 다르다 (실측 77706 vs 77903)

사용: mail_curate.py [--out PATH] [--json]
"""
from __future__ import annotations
import argparse, collections, hashlib, json, re, sys, unicodedata
from datetime import datetime
from pathlib import Path

PROJ = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJ / "agents" / "nworks"))
from daily_brief import _load_filter, brief_skip_reason      # noqa: E402  규칙 해석은 한 곳

T0 = "2026-08-12T15:29:53+09:00"
SERIES = {                      # 계정키: (상태, SSOT 접두)
    "{{BOT_USER}}": ("live", "rbp-"),
    "{{OPERATOR_USER}}":   ("frozen", "운영자-"),
}
# 결재·품의 알림은 시스템 발신이지만 **제목에 업무 사실이 완결돼 있다** —
# 기안자·문서종류·과제명·항목·날짜. 과제 정산과 주간보고의 1차 근거다 (운영자 결정 2026-10-01).
# brief_filter.json 은 손대지 않는다: 부서방 브리핑은 "지금 볼 것"을 고르는 일이라
# 결재 알림이 뜨지 않는 게 맞고, SSOT 는 "나중에 찾아볼 기록"이라 남는 게 맞다.
# 같은 규칙이 용도에 따라 다른 결과를 내야 하므로, 예외는 **승격 쪽에만** 둔다.
_APPROVAL_SENDER = "no-reply-workplace@navercorp.com"
_APPROVAL_SUBJ = re.compile(r"^\s*\[(결재|완료|참조|반려|상신|회수|기안|승인)\]")

# 판정 → SSOT 승격 여부. "업무에 필요한가" 가 기준이다.
PROMOTE = {
    "keep":   True,             # 업무 메일
    "개인":   True,             # 급여·인사 — 업무다. 비밀이 아니고(운영자 2026-09-07) 브리핑만 제외
    "본문제외": True,           # 제목은 업무, 본문만 가려진 것
    "결재기록": True,           # 네이버웍스 결재·품의 알림 — 업무 기록 (운영자 2026-10-01)
    "테스트":  False,
    "시스템알림": False,
    "마케팅":  False,
    "중복":   False,            # uid canonical 아닌 사본
}


def is_approval(frm: str, subj: str) -> bool:
    return frm.lower() == _APPROVAL_SENDER and bool(_APPROVAL_SUBJ.match(subj or ""))
HEAD = ["uid", "canonical", "copies", "received", "sent", "from", "to", "subject",
        "verdict", "promote", "series", "month", "mailid", "raw_path", "ssot_name",
        "attachments", "sensitivity"]


def nfc(s: str) -> str:
    return unicodedata.normalize("NFC", s or "")


def cell(v) -> str:
    """TSV 한 칸. 탭뿐 아니라 **개행도** 지운다 — 제목에 줄바꿈이 든 메일이 있고
    그 한 건이 대장의 행 전체를 밀어 열을 깨뜨린다 (2026-10-01 실측)."""
    return re.sub(r"[\t\r\n]+", " ", str(v if v is not None else "")).strip()


def subj_norm(s: str) -> str:
    """공백 축약 + 소문자. Re:/FW: 는 벗기지 않는다 — 답장은 다른 발송 사건이다."""
    return re.sub(r"\s+", " ", nfc(s)).strip().lower()


def uid_of(m: dict) -> str:
    frm = ((m.get("from") or {}).get("email") or "").lower()
    return hashlib.sha1(f"{m.get('sentTime','')}|{frm}|{subj_norm(m.get('subject'))}"
                        .encode()).hexdigest()[:16]                     # noqa: S324


def load_series(key: str) -> list[dict]:
    root = PROJ / "output" / "raw_mail" / key
    out = []
    for p in sorted(root.glob("*/[0-9]*.json")):
        try:
            j = json.loads(p.read_text(encoding="utf-8"))
        except Exception:                                               # noqa: BLE001
            continue
        m = j.get("mail") or j
        if "mailId" not in m:
            continue
        m["_series"], m["_month"] = key, p.parent.name
        # 첨부는 **응답 최상위**에 온다 (mail 객체 안이 아니다 — mail_collector 실측 2026-08-04).
        # j["mail"] 로 내려간 뒤 찾으면 언제나 0건이 된다 (2026-10-01 교정).
        atts = j.get("attachments") or m.get("attachments") or []
        m["_atts"] = len([a for a in atts
                          if not a.get("cid") and a.get("contentDisposition") != "inline"])
        # 수신자를 보관한다. 같은 발송이 부서원 3명에게 각각 와서 봇이 3통을 받는 일이 있고
        # (실측 rbp-335/336/337), 사본을 중복으로 접을 때 "누가 받았는가"를 잃으면 안 된다.
        m["_to"] = [(x.get("email") or "").lower()
                    for x in (m.get("to") or []) if x.get("email")]
        # stage md 파일명(= SSOT 파일명의 접두 뒤 부분). 색인 링크를 만들 때 쓴다
        st = sorted((PROJ / "output" / "stage" / f"mail_{key}" / p.parent.name)
                    .glob(f"{m['mailId']}_*.md"))
        m["_stage"] = st[0].name if st else ""
        out.append(m)
    return out


def write_ssot_index(rows: list[dict], out_dir: Path) -> int:
    """SSOT 메일 폴더용 통합 색인. **승격되는 것만** 싣는다.

    stage 의 `index.md` 는 계열마다 하나이고 **전량**이다(U 용). 그것을 SSOT 로 올리면
    승격되지 않은 업무무관 메일이 깨진 링크로 남는다 — 색인이 없는 파일을 가리키는 일은
    이미 한 번 사고가 됐다(files.tsv 유령 행, 2026-09-29). 그래서 SSOT 색인은 여기서
    대장을 근거로 다시 만든다. 두 계열이 한 폴더에 섞이므로 색인도 하나다.
    """
    live = [r for r in rows if r["promote"] == "Y" and r["_stage"]]
    live.sort(key=lambda r: r["received"], reverse=True)
    out_dir.mkdir(parents=True, exist_ok=True)
    span = (f"{live[-1]['received'][:10]} ~ {live[0]['received'][:10]}" if live else "비어 있음")
    ser = collections.Counter(r["series"] for r in live)
    doc = [
        "---", 'title: "연구기획사업부 메일 색인"', 'security: "S1 (사내 공개)"',
        "생성: 자동 (mail_curate.py — 손으로 고치지 않는다)", "---", "",
        "# 메일 색인", "",
        f"총 **{len(live)}건** · 수신 {span}", "",
        f"- `rbp-` 현행 계열 {ser.get('{{BOT_USER}}', 0)}건 — {{BOT_USER}}@ 수집. **여기 오는 모든 메일이 원본이다**",
        f"- `운영자-` 과거 계열 {ser.get('{{OPERATOR_USER}}', 0)}건 — {{OPERATOR_USER}}@ 수집분 (2026-06 ~ 08/12). 고정",
        "",
        f"업무와 무관한 메일(시스템 알림·마케팅·중복)은 이 폴더에 올리지 않는다. "
        f"원본은 전량 U `5. Naverworks/00_raw/mail_<계열>/` 에 있다.", "",
        "| 수신일 | 제목 | 발신 | 첨부 | 파일 |", "|---|---|---|---|---|",
    ]
    for r in live:
        subj = r["subject"].replace("|", "\\|")[:60]
        att = r["attachments"] if r["attachments"] not in ("", "0", 0) else ""
        # 링크 경로를 <> 로 감싼다. 제목에 닫는 괄호가 들어가면 (재발송) · (11/06, 금)
        # 마크다운이 거기서 링크를 끊는다 — 981개 중 135개가 깨졌다 (2026-10-01 실측).
        tgt = f"{r['month']}/{r['ssot_name']}_{r['_stage'].split('_', 1)[1]}"
        doc.append(f"| {r['received'][:10]} | {subj} | {r['from']} | {att} | "
                   f"[{r['ssot_name']}](<{tgt}>) |")
    (out_dir / "_색인.md").write_text("\n".join(doc) + "\n", encoding="utf-8")
    return len(live)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(PROJ / "output" / "stage" / "mail_대장.tsv"))
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()

    cfg = _load_filter()
    rows, warn = [], []
    for key, (state, prefix) in SERIES.items():
        mails = load_series(key)
        if not mails:
            warn.append(f"{key}: 원본 0건 — 계열이 비어 있다")
            continue
        # 계열 경계 검사: frozen 은 T0 이전만, live 는 T0 이후만이어야 한다
        bad = [m for m in mails
               if (state == "frozen") != ((m.get("receivedTime") or "") < T0)
               and (m.get("receivedTime") or "") != T0]
        if bad:
            warn.append(f"{key}({state}): T0 경계 위반 {len(bad)}건 — 계열 분리가 깨졌다")
        for m in mails:
            frm = ((m.get("from") or {}).get("email") or "")
            why = brief_skip_reason(m.get("subject", ""), frm, cfg)
            if why and is_approval(frm, m.get("subject", "")):
                why = "결재기록"      # 브리핑은 계속 거르고, SSOT 에는 남긴다
            rows.append({
                "uid": uid_of(m), "received": m.get("receivedTime", ""),
                "sent": m.get("sentTime", ""),
                "from": frm,
                "to": ";".join(m["_to"]), "_to": m["_to"],
                "month": m["_month"], "_stage": m["_stage"],
                "subject": cell(nfc(m.get("subject")))[:200],
                "verdict": why or "keep", "series": m["_series"],
                "mailid": m["mailId"],
                "raw_path": f"00_raw/mail_{key}/{m['_month']}/{m['mailId']}.json",
                "ssot_name": f"{prefix}{m['mailId']}", "attachments": m["_atts"],
                "sensitivity": "", "_state": state,
            })

    # uid 묶기 → canonical 선정: live 계열 우선, 그 안에서 mailId 작은 것
    by_uid = collections.defaultdict(list)
    for r in rows:
        by_uid[r["uid"]].append(r)
    for u, g in by_uid.items():
        g.sort(key=lambda r: (r["_state"] != "live", int(r["mailid"])))
        canon = g[0]
        copies = ";".join(f"{x['series']}#{x['mailid']}" for x in g[1:])
        for i, r in enumerate(g):
            r["canonical"] = canon["ssot_name"]
            r["copies"] = copies if i == 0 else ""
            if i:                                   # 사본은 승격하지 않는다
                r["verdict"] = "중복"
        canon["attachments"] = max(x["attachments"] for x in g)
        # 사본의 수신자를 canonical 로 합친다 — 접어도 수신 범위는 보존된다
        canon["to"] = ";".join(sorted({t for x in g for t in x["_to"]}))

    for r in rows:
        r["promote"] = "Y" if PROMOTE.get(r["verdict"], True) else "N"

    rows.sort(key=lambda r: r["received"], reverse=True)
    out = Path(a.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", encoding="utf-8", newline="") as fh:
        fh.write("\t".join(HEAD) + "\n")
        for r in rows:
            fh.write("\t".join(cell(r.get(k, "")) for k in HEAD) + "\n")

    n_idx = write_ssot_index(rows, PROJ / "output" / "stage" / "mail_index")

    stat = collections.Counter(r["verdict"] for r in rows)
    prom = collections.Counter(r["promote"] for r in rows)
    ser = collections.Counter(r["series"] for r in rows)
    newest = rows[0]["received"] if rows else ""
    summary = {"rows": len(rows), "uid": len(by_uid), "series": dict(ser),
               "verdict": dict(stat), "promote": dict(prom),
               "newest_received": newest, "out": str(out), "warn": warn}
    if a.json:
        print(json.dumps(summary, ensure_ascii=False))
        return 1 if warn else 0
    print(f"대장 {len(rows)}행 · uid {len(by_uid)} · 계열 {dict(ser)}")
    print(f"  판정  {dict(stat)}")
    print(f"  승격  Y {prom.get('Y',0)} · N {prom.get('N',0)}")
    print(f"  최신 수신 {newest}")
    print(f"  → {out}")
    print(f"  → SSOT 색인 {n_idx}행  stage/mail_index/_색인.md")
    for w in warn:
        print(f"  ! {w}")
    return 1 if warn else 0


if __name__ == "__main__":
    raise SystemExit(main())
