#!/usr/bin/env python3
"""정체를 모르는 데이터를 모아 AX_TF 방에 올릴 요청 초안을 만든다.

    python3 scripts/원장/분류요청생성.py [--상한 12]

**초안까지만 만든다. 발송하지 않는다.** 외부 전송은 Roy 승인이 있어야 한다.

왜 필요한가
    어느 과제에도 붙지 않은 관측을 기계가 추측해서 붙이면 그 순간 화면이 거짓말을 한다.
    미분류로 두는 편이 틀린 분류보다 낫다. 다만 **미분류로 두고 끝내면 영영 모른다.**
    그래서 사람에게 묻는다 — 다만 **사람이 답할 수 있는 만큼만** 묻는다.

왜 상한이 있는가
    성장 질문 88건 중 48건이 답 없이 만료됐다. 사람의 처리량이 상한이고 이미 초과했다.
    한 번에 12건을 넘기지 않는다. 답이 오면 별칭으로 굳고 다음 회차부터 기계가 푼다.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import re
from pathlib import Path

PROJ = Path(__file__).resolve().parents[2]
OUT = PROJ / "output" / "원장"
ASK = PROJ / "workspace" / "재정비" / "분류요청"
DEFS = Path(__file__).resolve().parent / "과제정의.json"

# 물어봐야 소용없는 것. 기계 알림과 사내 잡무는 과제가 아니다.
SKIP = re.compile(
    r"다이제스트|멘션|보안 알림|인증번호|newsletter|unsubscribe|webinar|이력서|"
    r"급상여|급여|연차|휴가|점심|저녁|커피|생일|수시접수|세미나 참석|근무\\s*결과|부재\\s*일정|근태|출퇴근|지출결의서|면허세|"
    r"Confluence|Slack|Notion|ChatGPT|Claude|Google|Atlassian|NAVER WORKS", re.I)

# 대화체. 사람끼리 주고받은 말이지 과제에 붙일 일이 아니다.
CHAT = re.compile(r"[ㅋㅎㅠㅜ]|하실까요|할까요|하시죠|드릴께요|드릴게요|주세요~|"
                  r"괜찮으세요|고생|감사합니다|^넵|알겠습니다|확인했어요|등록확인|"
                  r"죄송|잘 다녀오|좋죠|잼있|재밌|개인 일정|개인일정|미룰 수|"
                  r"저는 개인적으로|궁금합니다")

# 봇 명령은 분류 대상이 아니라 **원장에 반영할 정정**이다 (별건: 봇 정정 파서).
BOT_CMD = re.compile(r"@\s*Urban\s*AX\s*Bot|「.+?」\s*(완료|취소|지속)")

# 업무 신호. **이것이 하나도 없으면 묻지 않는다.**
# 미분류를 묻는 것은 좋지만 아무거나 묻는 것은 나쁘다. 성장 질문 88건 중 48건이
# 답 없이 만료된 것은 물어볼 것을 고르지 않았기 때문이다.
WORK = re.compile(
    r"제출|승인|집행|계약|협약|평가|심사|실사|보고|마감|기한|공고|제안서|견적|정산|"
    r"신청|납부|송부|회신|지급|이체|증빙|세금계산서|발주|검수|착수|종료|변경|"
    r"착수보고|중간보고|최종보고|워크샵|워크숍|간담회|위원회|공문|서류|양식|"
    r"\\d+\\s*원|\\d+\\s*억|\\d+\\s*만원|기관|사업|과제|용역|바우처|인건비|사업비|예산")


def load_latest() -> dict:
    js = OUT / "최신.js"
    if not js.exists():
        return {}
    m = re.search(r"var OPS_DATA = (\{.*\});", js.read_text(encoding="utf-8"), re.S)
    return json.loads(m.group(1)) if m else {}


def worth_asking(it: dict) -> bool:
    """업무 신호가 있고 잡담이 아닌 것만. 물을 것을 고르지 않으면 답이 오지 않는다."""
    t = it.get("문장") or ""
    if len(t) < 12 or SKIP.search(t):
        return False
    if not WORK.search(t) or BOT_CMD.search(t):
        return False
    src = it.get("출처") or ""
    if ("부서방" in src or "AX_TF" in src) and CHAT.search(t):
        return False          # 사람끼리의 대화는 과제에 붙일 일이 아니다
    return True


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--상한", dest="cap", type=int, default=12)
    a = ap.parse_args()

    data = load_latest()
    unl = data.get("미분류", [])
    defs = json.loads(DEFS.read_text(encoding="utf-8"))["과제"]

    asked_path = ASK / "_기질문.json"
    asked = set(json.loads(asked_path.read_text(encoding="utf-8"))) \
        if asked_path.exists() else set()

    def key(it):
        return re.sub(r"[^0-9가-힣A-Za-z]", "", it.get("문장") or "")[:60]

    cand = [it for it in unl if worth_asking(it) and key(it) not in asked]
    # 사람이 쓴 것 먼저, 그다음 최신순
    cand.sort(key=lambda it: (0 if "방" in (it.get("출처") or "") or "AX_TF" in (it.get("출처") or "")
                              else 1, it.get("일자") or ""), reverse=False)
    cand.sort(key=lambda it: (0 if ("부서방" in (it.get("출처") or "")
                                    or "AX_TF" in (it.get("출처") or "")) else 1,
                              "9999" if not it.get("일자") else ""))
    cand = sorted(cand, key=lambda it: it.get("일자") or "", reverse=True)[:a.cap]

    today = dt.date.today().isoformat()
    names = " · ".join(f"{p['no']}.{p['이름'][:14]}" for p in defs)

    L = [f"# AX_TF 분류 요청 초안 — {today}", "",
         f"미분류 관측 **{len(unl)}건** 중 사람만 답할 수 있는 **{len(cand)}건**입니다. "
         "기계가 추측해서 붙이지 않았습니다.", "",
         "**이 문서는 초안입니다. 발송하지 않았습니다.**", "",
         "## 방에 올릴 문안", "", "```",
         f"[운영 OS] 분류 요청 {len(cand)}건 · {today}", "",
         "아래 항목이 어느 과제인지 확인 부탁드립니다.",
         "어느 과제도 아니면 「해당없음」이라고 답해 주시면 다음부터 묻지 않습니다.", ""]
    for i, it in enumerate(cand, 1):
        src = (it.get("출처") or "").split(" · ")[0]
        L.append(f"{i}. [{(it.get('일자') or '')[5:]}] {(it.get('문장') or '')[:90]}  ({src})")
    L += ["", f"과제: {names}", "```", ""]

    # 귀속이 갈린 건은 사람이 손으로 적는다. 이 파일은 회차마다 덮어써지므로
    # 여기 적으면 다음 회차에 사라진다 (2026-09-14 실제로 사라졌다). 가리키기만 한다.
    dis = sorted((PROJ / "workspace" / "재정비" / "귀속분쟁").glob("*.md"), reverse=True)
    if dis:
        L += [f"## 귀속이 갈린 것", "",
              f"사람 표와 원장의 답이 갈린 건이 따로 정리돼 있습니다 — "
              f"`workspace/재정비/귀속분쟁/{dis[0].name}`", ""]

    L += ["## 답이 오면", "",
          "1. 답한 과제의 별칭을 `scripts/원장/과제정의.json` 에 추가합니다",
          "2. 다음 회차부터 같은 문구가 자동으로 그 과제에 붙습니다",
          "3. 「해당없음」은 `_기질문.json` 에 남겨 다시 묻지 않습니다", "",
          "## 묻지 않은 것", "",
          f"- 기계 알림·사내 잡무로 걸러낸 것: {len(unl) - len([x for x in unl if worth_asking(x)])}건",
          f"- 이미 물어본 것: {len([x for x in unl if key(x) in asked])}건",
          f"- 상한({a.cap}건)을 넘어 다음 회차로 미룬 것: "
          f"{max(0, len([x for x in unl if worth_asking(x) and key(x) not in asked]) - a.cap)}건", "",
          "> 한 번에 12건을 넘기지 않습니다. 성장 질문 88건 중 48건이 답 없이 만료된 적이 있습니다. "
          "사람의 처리량이 상한입니다.", ""]

    ASK.mkdir(parents=True, exist_ok=True)
    (ASK / f"요청_{today}.md").write_text("\n".join(L), encoding="utf-8")
    print(f"· 미분류 {len(unl)} → 물을 것 {len(cand)}건")
    print(f"  → workspace/재정비/분류요청/요청_{today}.md  (초안 · 발송 안 함)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
