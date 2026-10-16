#!/usr/bin/env python3
"""대조 — 시스템 등록 집행 ↔ 통장·카드 실거래.

두 축을 본다.
  L4↔L5  시스템에 등록된 집행 vs 통장에서 실제로 나간 돈  → 미등록·미지출
  L3↔L4  계획 vs 실제                                  → 진도 이탈·소진 속도

차이가 났을 때 **단정하지 않는다.** 사유 후보를 붙이고 "확인 필요"로 남긴다.
틀린 대조가 맞는 것처럼 보이는 것이 이 도구의 가장 큰 위험이다.
"""
from __future__ import annotations

import datetime as dt
import re
from dataclasses import dataclass, field

from ingest import BankTx, CardTx, ExecLine

PAYROLL_HINT = re.compile(r"인건비|급여|보수")
CARD_HINT = re.compile(r"카드")


@dataclass
class Finding:
    level: str          # 확인필요 | 참고 | 정상
    title: str
    amount: float = 0.0
    detail: str = ""
    cause: str = ""     # 사유 후보 — 단정이 아니다


@dataclass
class Reconciliation:
    project: str
    as_of: str
    sys_total: float = 0.0
    bank_out: float = 0.0
    bank_in: float = 0.0
    card_total: float = 0.0
    findings: list[Finding] = field(default_factory=list)

    @property
    def needs_check(self) -> list[Finding]:
        return [f for f in self.findings if f.level == "확인필요"]


def _monthly_unit(bank: list[BankTx]) -> tuple[float, int]:
    """통장에서 반복되는 월 인건비 이체 금액과 횟수를 찾는다."""
    amounts: dict[float, int] = {}
    for t in bank:
        if t.outflow and PAYROLL_HINT.search(t.memo):
            amounts[t.outflow] = amounts.get(t.outflow, 0) + 1
    if not amounts:
        return 0.0, 0
    unit, n = max(amounts.items(), key=lambda kv: kv[1])
    return unit, n


def reconcile(project: str, as_of: str, lines: list[ExecLine], total: ExecLine | None,
              bank: list[BankTx], card: list[CardTx]) -> Reconciliation:
    r = Reconciliation(project=project, as_of=as_of)
    r.sys_total = total.spent if total else sum(x.spent for x in lines)
    r.bank_out = sum(t.outflow for t in bank)
    r.bank_in = sum(t.inflow for t in bank)
    r.card_total = sum(c.registered for c in card)

    # ── 인건비: 통장 지급 개월 vs 시스템 등록 개월 ─────────────────────────
    unit, months_paid = _monthly_unit(bank)
    pay_line = next((x for x in lines if PAYROLL_HINT.search(x.name)), None)
    if unit and pay_line:
        months_reg = round(pay_line.spent / unit, 2) if unit else 0
        gap_months = months_paid - months_reg
        if abs(gap_months) >= 0.99:
            gap = unit * gap_months
            r.findings.append(Finding(
                "확인필요",
                f"인건비 — 통장 {months_paid}개월 지급 / 시스템 {months_reg:g}개월 등록",
                gap,
                f"월 단가 {unit:,.0f}원 기준 {abs(gap_months):g}개월 차이",
                "미등록이거나 다른 계정으로 처리됐을 수 있다. 정산 시 증빙 없는 지출로 잡힐 수 있어 확인이 필요하다."))
        else:
            r.findings.append(Finding(
                "정상", f"인건비 — 통장·시스템 {months_paid}개월로 일치", pay_line.spent))

    # ── 카드: 등록액 vs 비목 집행액 ────────────────────────────────────────
    card_bank = sum(t.outflow for t in bank if CARD_HINT.search(t.memo))
    if r.card_total and card_bank:
        d = r.card_total - card_bank
        if abs(d) > 1:
            r.findings.append(Finding(
                "확인필요", "카드 — 매입내역 합계와 통장 결제액 불일치", d,
                f"매입 {r.card_total:,.0f} / 통장 결제 {card_bank:,.0f}",
                "청구 시점 차이(사용일↔청구일)일 수 있다."))
        else:
            r.findings.append(Finding(
                "정상", "카드 — 매입내역과 통장 결제액 일치", r.card_total))

    # 카드 매입 ↔ 해당 비목 집행액 (부가세 분리 여부)
    cand = [x for x in lines if x.name in ("연구과제추진비", "연구활동비") and x.spent]
    if r.card_total and cand:
        line = max(cand, key=lambda x: x.spent)
        d = r.card_total - line.spent
        if abs(d) > 1:
            ratio = r.card_total / line.spent if line.spent else 0
            r.findings.append(Finding(
                "확인필요", f"카드 등록액 vs {line.name} 집행액", d,
                f"카드 {r.card_total:,.0f} / 시스템 {line.spent:,.0f} (배율 {ratio:.3f})",
                "카드는 총액(부가세 포함), 시스템은 공급가액으로 잡혔을 수 있다. 부가세 비목 처리 확인 필요."))

    # ── 통장 출금 총액 vs 시스템 집행 총액 ─────────────────────────────────
    d = r.bank_out - r.sys_total
    if abs(d) > 1:
        r.findings.append(Finding(
            "확인필요", "통장 출금 총액 vs 시스템 집행 총액", d,
            f"통장 {r.bank_out:,.0f} / 시스템 {r.sys_total:,.0f}",
            "위 항목별 차이의 합계다. 개별 사유가 해소되면 이 값도 맞아야 한다."))
    else:
        r.findings.append(Finding("정상", "통장 출금과 시스템 집행 총액 일치", r.sys_total))

    # ── 시스템 미등록 의심 거래 ───────────────────────────────────────────
    known = re.compile(r"인건비|급여|보수|카드")
    unknown = [t for t in bank if t.outflow and not known.search(t.memo)]
    for t in unknown:
        r.findings.append(Finding(
            "확인필요", f"시스템 대응 미확인 출금 — {t.memo or '(적요 없음)'}", t.outflow,
            f"{t.date}", "해당 지출이 시스템에 등록됐는지 확인 필요."))
    return r


# --------------------------------------------------------------------------- 계획

@dataclass
class Plan:
    months_left: float
    monthly_payroll: float
    lines: list[tuple[str, float, float, float]] = field(default_factory=list)
    # (비목, 잔액, 월 가용, 잔여기간 대비 소진율 편차)


def plan(lines: list[ExecLine], end_date: str, monthly_payroll: float,
         today: dt.date | None = None) -> Plan:
    """잔여 예산 ÷ 잔여 기간. 계획이 없으면 '여기까지'를 못 박을 수 없다."""
    today = today or dt.date.today()
    end = dt.date.fromisoformat(end_date)
    months_left = max((end - today).days / 30.44, 0.1)
    p = Plan(months_left=round(months_left, 1), monthly_payroll=monthly_payroll)
    for x in lines:
        base = x.revised or x.budget
        if not base:
            continue
        per_month = x.remain / months_left if months_left else 0
        # 소진 속도 편차: 시간 경과율 대비 집행률
        p.lines.append((x.name, x.remain, per_month, x.rate))
    return p
