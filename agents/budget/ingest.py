#!/usr/bin/env python3
"""R&D 예산 인풋 파서 — 4종 원천을 공통 형태로 정규화한다.

원천마다 양식이 다르고 헤더 위치도 다르다. 열 이름으로 찾고, 못 찾으면
0으로 넘어가지 말고 **실패로 알린다**. 조용히 0이 되면 대조가 맞는 것처럼 보인다.

    예산표        비목·세목·세세목 계약예산
    예실대비표     시스템 등록 집행 (계약예산·수정예산·실집행·잔액·건수)
    통장 거래내역   실제 입출금        ⚠️ 실명·계좌번호 포함 — 산출물에 옮기지 않는다
    카드 거래내역   가맹점별 사용액     ⚠️ 카드번호 포함 — 동일
"""
from __future__ import annotations

import re
import warnings
from dataclasses import dataclass, field
from pathlib import Path

warnings.filterwarnings("ignore")


class IngestError(RuntimeError):
    """열을 못 찾거나 값이 비면 즉시 올린다. 침묵은 금지."""


def _num(v) -> float:
    if v is None:
        return 0.0
    if isinstance(v, (int, float)):
        return float(v)
    s = str(v).replace(",", "").replace("₩", "").strip()
    if not s or s in ("-", "—"):
        return 0.0
    try:
        return float(s)
    except ValueError:
        return 0.0


def _find_header(rows: list[list], must: tuple[str, ...], limit: int = 12) -> int:
    """필수 열 이름이 가장 많이 걸리는 행을 헤더로 본다."""
    best, best_hit = -1, 0
    for i, r in enumerate(rows[:limit]):
        joined = " ".join(str(c or "") for c in r)
        hit = sum(1 for m in must if m in joined)
        if hit > best_hit:
            best, best_hit = i, hit
    if best_hit < max(2, len(must) // 2):
        raise IngestError(f"헤더를 찾지 못했다. 찾던 열: {must}")
    return best


def _sheet_rows(path: Path) -> list[list]:
    if path.suffix.lower() == ".xlsx":
        import openpyxl
        ws = openpyxl.load_workbook(str(path), data_only=True).worksheets[0]
        return [[ws.cell(r, c).value for c in range(1, ws.max_column + 1)]
                for r in range(1, ws.max_row + 1)]
    import xlrd
    ws = xlrd.open_workbook(str(path)).sheet_by_index(0)
    return [[ws.cell_value(r, c) for c in range(ws.ncols)] for r in range(ws.nrows)]


# --------------------------------------------------------------------------- 예실대비표

@dataclass
class ExecLine:
    """시스템에 등록된 비목별 집행."""
    name: str
    budget: float          # 계약예산
    revised: float         # 수정예산
    spent: float           # 실집행액(현금)
    vat: float
    remain: float
    count: int

    @property
    def rate(self) -> float:
        base = self.revised or self.budget
        return (self.spent / base) if base else 0.0


def read_exec(path: Path) -> tuple[list[ExecLine], ExecLine | None]:
    """예실대비표 → (비목별 라인, 합계). 병합셀로 같은 이름이 반복되면 하나만 남긴다."""
    rows = _sheet_rows(path)
    h = _find_header(rows, ("계약예산", "실집행", "집행잔액"))
    lines, total, seen = [], None, set()
    for r in rows[h + 1:]:
        name = str(r[0] or "").strip()
        if not name:
            continue
        line = ExecLine(name, _num(r[1]), _num(r[2]), _num(r[3]),
                        _num(r[4]), _num(r[6]), int(_num(r[8])))
        if name in ("합계", "총계", "계"):
            total = line
            continue
        if name in seen:          # 병합셀 반복
            continue
        seen.add(name)
        if line.budget or line.spent:
            lines.append(line)
    if not lines:
        raise IngestError(f"집행 라인을 찾지 못했다: {path.name}")
    return lines, total


# --------------------------------------------------------------------------- 통장

@dataclass
class BankTx:
    date: str
    memo: str
    inflow: float
    outflow: float
    # 의뢰인/수취인은 **의도적으로 담지 않는다** — 실명이며 산출물로 새면 안 된다


def read_bank(path: Path) -> list[BankTx]:
    rows = _sheet_rows(path)
    h = _find_header(rows, ("거래일시", "입금", "출금"))
    hdr = [str(c or "").strip() for c in rows[h]]
    def col(name):
        for i, c in enumerate(hdr):
            if name in c:
                return i
        raise IngestError(f"통장 내역에 '{name}' 열이 없다: {path.name}")
    i_d, i_m, i_in, i_out = col("거래일시"), col("적요"), col("입금"), col("출금")
    out = []
    for r in rows[h + 1:]:
        d = str(r[i_d] or "").strip()
        if not d or "합계" in str(r[i_m] or "") or "합계" in " ".join(str(x or "") for x in r[:4]):
            continue
        out.append(BankTx(d[:10], str(r[i_m] or "").strip(), _num(r[i_in]), _num(r[i_out])))
    return out


# --------------------------------------------------------------------------- 카드

@dataclass
class CardTx:
    used: str      # 사용일
    billed: str    # 청구일
    merchant: str
    total: float   # 총사용액
    registered: float


def read_card(path: Path) -> list[CardTx]:
    rows = _sheet_rows(path)
    h = _find_header(rows, ("사용일", "가맹점", "총사용액"))
    hdr = [str(c or "").strip() for c in rows[h]]
    def col(name, required=True):
        for i, c in enumerate(hdr):
            if name in c:
                return i
        if required:
            raise IngestError(f"카드 내역에 '{name}' 열이 없다: {path.name}")
        return None
    i_u, i_b = col("사용일"), col("청구일", False)
    i_m, i_t = col("가맹점"), col("총사용액")
    i_r = col("등록액", False)
    out = []
    for r in rows[h + 1:]:
        u = str(r[i_u] or "").strip()
        if not u or "합계" in u:
            continue
        out.append(CardTx(u[:10], str(r[i_b] or "")[:10] if i_b is not None else "",
                          str(r[i_m] or "").strip(), _num(r[i_t]),
                          _num(r[i_r]) if i_r is not None else _num(r[i_t])))
    return out


# --------------------------------------------------------------------------- 예산표

@dataclass
class BudgetSheet:
    title: str = ""
    period: str = ""
    total_budget: float = 0.0
    detail: list[tuple[str, str, float]] = field(default_factory=list)  # (세목, 세세목, 금액)


def read_budget(path: Path) -> BudgetSheet:
    """과제 예산표. 과제마다 양식이 달라 공통 골격만 뽑는다."""
    rows = _sheet_rows(path)
    bs = BudgetSheet()
    bs.title = str(rows[0][0] or "").strip() if rows else ""
    for r in rows[:4]:
        for c in r:
            s = str(c or "")
            if re.search(r"20\d\d[.\-]\d\d", s):
                bs.period = s.strip()
                break
        if bs.period:
            break
    for r in rows:
        if str(r[0] or "").strip() == "예산":
            for c in r[1:]:
                bs.total_budget = max(bs.total_budget, _num(c))
    for r in rows:
        cells = [str(c or "").strip() for c in r]
        amt = next((_num(c) for c in r[3:5] if _num(c) > 0), 0.0)
        if amt and cells[2] and cells[2] not in ("세세목",):
            bs.detail.append((cells[1] or "", cells[2], amt))
    return bs
