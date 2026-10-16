#!/usr/bin/env python3
"""과제별 예산 보고서 생성 — SSOT `{{DEPT_ROOT}}/20_예산` 에 들어갈 아웃풋.

    python3 build_report.py PRJ-N003

산출 (config 의 과제코드별):
    output/<과제코드>/현재상태_<YYMMDD>.md
    output/<과제코드>/대조_<YYMMDD>.md
    output/<과제코드>/집행계획_<YYMMDD>.md

⚠️ 산출물에는 **실명·계좌번호·카드번호를 넣지 않는다.**
   SSOT 반입 금지 규정(개인식별+금전 결합)에 걸리기 때문이며,
   원천은 U에 두고 경로만 참조한다.
"""
from __future__ import annotations

import datetime as dt
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from ingest import read_bank, read_budget, read_card, read_exec  # noqa: E402
from reconcile import plan, reconcile  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
DRIVE = (Path.home() / "Library/CloudStorage"
         / "GoogleDrive-<계정>/공유 드라이브")


def won(v: float) -> str:
    return f"{v:,.0f}"


def load(code: str):
    cfg = json.loads((ROOT / "config" / "과제" / f"{code}.json").read_text(encoding="utf-8"))
    base = DRIVE / cfg["원천폴더"]
    i = cfg["인풋"]
    return cfg, base, (read_exec(base / i["예실대비표"]),
                       read_bank(base / i["통장"]),
                       read_card(base / i["카드"]),
                       read_budget(base / i["예산표"]))


def head(cfg: dict, kind: str, asof: str) -> str:
    return (f"---\n"
            f"과제: {cfg['code']} {cfg['name']}\n"
            f"종류: {kind}\n"
            f"기준일: {asof}\n"
            f"생성: {dt.datetime.now():%Y-%m-%d %H:%M}\n"
            f"민감도: 사내한정\n"
            f"원천: {cfg['원천폴더']}\n"
            f"---\n\n")


def report_status(cfg, lines, total, bs, asof) -> str:
    s = head(cfg, "현재상태", asof)
    s += f"# 현재 상태 — {cfg['name']}\n\n"
    base = total.revised or total.budget if total else 0
    s += (f"| | 금액 |\n|---|---:|\n"
          f"| 계약예산 | {won(base)} |\n"
          f"| 실집행 | {won(total.spent if total else 0)} |\n"
          f"| 집행잔액 | {won(total.remain if total else 0)} |\n"
          f"| 집행률 | {(total.rate*100 if total else 0):.1f}% |\n\n")
    s += "## 비목별\n\n| 비목 | 계약예산 | 실집행 | 잔액 | 집행률 | 건수 |\n|---|---:|---:|---:|---:|---:|\n"
    for x in lines:
        s += (f"| {x.name} | {won(x.revised or x.budget)} | {won(x.spent)} | "
              f"{won(x.remain)} | {x.rate*100:.1f}% | {x.count} |\n")
    s += (f"\n> 예산표 총액 {won(bs.total_budget)} · 예실대비표 계약예산 {won(base)}"
          f"{' — **불일치**' if abs(bs.total_budget - base) > 1 else ' — 일치'}\n")
    return s


def report_recon(cfg, r, asof) -> str:
    s = head(cfg, "대조", asof)
    s += f"# 대조 — {cfg['name']}\n\n"
    s += ("시스템에 등록된 집행과 통장·카드 실거래를 맞춘 결과다.\n"
          "**차이는 단정하지 않는다.** 사유 후보를 붙였을 뿐이며 담당자 확인이 필요하다.\n\n")
    s += (f"| | 금액 |\n|---|---:|\n"
          f"| 시스템 집행 | {won(r.sys_total)} |\n"
          f"| 통장 출금 | {won(r.bank_out)} |\n"
          f"| 통장 입금 | {won(r.bank_in)} |\n"
          f"| 카드 등록액 | {won(r.card_total)} |\n\n")

    need = r.needs_check
    s += f"## 확인 필요 {len(need)}건\n\n"
    for f in need:
        s += f"### {f.title}\n\n"
        if f.amount:
            s += f"- 금액 **{won(f.amount)}** {f'· {f.detail}' if f.detail else ''}\n"
        if f.cause:
            s += f"- {f.cause}\n"
        s += "\n"
    ok = [f for f in r.findings if f.level == "정상"]
    if ok:
        s += "## 일치 확인\n\n" + "".join(f"- {f.title}\n" for f in ok) + "\n"

    # 차이 합산 검산 — 개별 사유가 총 차이를 설명하는지
    gap = r.bank_out - r.sys_total
    parts = [f for f in need if f.title != "통장 출금 총액 vs 시스템 집행 총액"]
    acc = sum(f.amount for f in parts)
    s += (f"## 검산\n\n총 차이 {won(gap)} · 개별 사유 합 {won(acc)} → "
          f"{'**전액 설명됨**' if abs(gap-acc) < 1 else f'미설명 {won(gap-acc)}'}\n")
    return s


def report_plan(cfg, lines, p, asof) -> str:
    s = head(cfg, "집행계획", asof)
    s += f"# 집행 계획 — {cfg['name']}\n\n"
    s += (f"사업 종료 **{cfg['기간']['종료']}** · 회계연도 종료 "
          f"**{(cfg.get('회계연도') or cfg['기간'])['종료']}** · 잔여 **{p.months_left}개월** · "
          f"월편성 인건비 {won(p.monthly_payroll)}\n\n"
          f"> 인풋 기준일은 **{asof}** 이다. 그 이후 지급·등록분은 반영되지 않았다.\n\n")
    kinds = cfg.get("비목성격", {})
    fixed, at_end = set(kinds.get("고정", [])), set(kinds.get("종료시", []))
    elapsed = _fy_elapsed(cfg)
    s += "## 비목별 월 가용액\n\n| 비목 | 성격 | 잔액 | 월 가용 | 집행률 | 판단 |\n|---|---|---:|---:|---:|---|\n"
    for name, remain, per_month, rate in p.lines:
        if name in fixed:
            kind, judge = "고정", _fixed_judge(
                remain, _budget_of(lines, name), _fy_months(cfg), p)
        elif name in at_end:
            kind, judge = "종료시", "정산 시점 집행 — 기간 경과율로 판단하지 않음"
        elif not remain:
            kind, judge = "변동", "소진"
        elif rate < elapsed - 0.15:
            kind, judge = "변동", "**지연 — 불용 위험**"
        elif rate > elapsed + 0.15:
            kind, judge = "변동", "**초과 — 증액 검토**"
        else:
            kind, judge = "변동", "정상"
        s += f"| {name} | {kind} | {won(remain)} | {won(per_month)} | {rate*100:.1f}% | {judge} |\n"
    s += (f"\n> **변동비**만 기간 경과율({elapsed*100:.0f}%, 회계연도 기준) 대비 ±15%p로 판단한다.\n"
          f"> 고정비(인건비)는 월편성액이 자동 소진되므로 **남은 개월수와 잔액**으로 본다.\n"
          f"> 종료시 집행 비목(일반관리비·위탁정산수수료·부가세)은 이 기준을 적용하지 않는다.\n")
    if cfg.get("메모"):
        s += f"\n## 과제 제약\n\n{cfg['메모']}\n"
    return s


def _budget_of(lines, name: str) -> float:
    for x in lines:
        if x.name == name:
            return x.revised or x.budget
    return 0.0


def _fy_months(cfg) -> float:
    fy = cfg.get("회계연도") or cfg["기간"]
    a, b = dt.date.fromisoformat(fy["시작"]), dt.date.fromisoformat(fy["종료"])
    return max((b - a).days / 30.44, 1)


def _fy_elapsed(cfg) -> float:
    """회계연도 경과율. 사업 전체 기간이 아니라 **해당 연도 예산** 기준이어야 한다."""
    fy = cfg.get("회계연도") or cfg["기간"]
    a, b = dt.date.fromisoformat(fy["시작"]), dt.date.fromisoformat(fy["종료"])
    today = dt.date.today()
    return min(max((today - a).days / max((b - a).days, 1), 0), 1)


def _fixed_judge(remain: float, budget: float, fy_months: float, p) -> str:
    """고정비 — 잔액이 남은 개월수와 맞는가.

    월단가는 **그 비목 자신의 연간 예산 ÷ 회계연도 개월수**로 구한다.
    세세목(책임연구원·연구원·연구보조원)마다 단가가 다르므로
    전체 월편성액을 쓰면 세세목 판단이 전부 틀린다.
    """
    unit = budget / fy_months if fy_months else 0
    if not unit:
        return "판단 불가 (예산 없음)"
    months_covered = remain / unit
    d = months_covered - p.months_left
    if d > 0.9:
        return f"잔액이 {months_covered:.1f}개월치 — 잔여 {p.months_left}개월보다 **{d:.1f}개월 많음**"
    if d < -0.9:
        return f"잔액이 {months_covered:.1f}개월치 — 잔여 {p.months_left}개월에 **{abs(d):.1f}개월 부족**"
    return f"잔액 {months_covered:.1f}개월치 — 잔여 기간과 맞음"


def main() -> int:
    if len(sys.argv) < 2:
        print("사용: build_report.py <과제코드>")
        return 2
    code = sys.argv[1]
    cfg, base, ((lines, total), bank, card, bs) = load(code)
    asof = max((cfg["인풋"]["예실대비표"][-10:-5], ""), key=len)
    asof = f"20{asof[:2]}-{asof[2:4]}-{asof[4:6]}" if asof[:2].isdigit() else dt.date.today().isoformat()

    r = reconcile(cfg["name"], asof, lines, total, bank, card)
    p = plan(lines, (cfg.get("회계연도") or cfg["기간"])["종료"], cfg["월편성_인건비"])

    out = ROOT / "output" / code
    out.mkdir(parents=True, exist_ok=True)
    stamp = dt.date.today().strftime("%y%m%d")
    for kind, text in (("현재상태", report_status(cfg, lines, total, bs, asof)),
                       ("대조", report_recon(cfg, r, asof)),
                       ("집행계획", report_plan(cfg, lines, p, asof))):
        (out / f"{kind}_{stamp}.md").write_text(text, encoding="utf-8")
        print(f"· {kind}_{stamp}.md")
    print(f"\n확인 필요 {len(r.needs_check)}건 · 잔여 {p.months_left}개월")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
