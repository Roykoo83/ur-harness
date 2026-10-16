"""HR data collector for Urban Data Lab personnel and project allocations.
"""

from datetime import datetime
import logging
import os
import re
from typing import Any, Iterator, Optional
import openpyxl

from rpbd.collect.base import Collector, RunContext, make_record

DEFAULT_HR_EXCEL_PATH = (
    "{{MOUNT_ROOT}}/"
    "공유 드라이브/U/2. R&D 사업/2-3. 수행중인 사업/"
    "어반 인력 및 과제 참여율 현황_260722.xlsx"
)

# Relative source path for C1 record provenance
RELATIVE_SOURCE_PATH = (
    "2. R&D 사업/2-3. 수행중인 사업/어반 인력 및 과제 참여율 현황_260722.xlsx"
)

# Columns to strictly extract (1-indexed for openpyxl)
# Sensitive columns (Col 4: Researcher No, Col 7: DOB, Col 25+: Payroll/Salaries) are NEVER read.
COL_ROW_NUM = 1
COL_ORG = 2
COL_NAME = 3
COL_DEPT = 5
COL_POSITION = 6
COL_DEGREE_MAJOR = 8
COL_JOIN_DATE = 9
COL_RESIGN_DATE = 10
COL_NOTE = 24

TASK_COLUMNS = [11, 12, 13, 14, 15, 16, 17, 19, 20, 21]


def parse_date(val: Any) -> Optional[str]:
    """Parse date cell value into ISO YYYY-MM-DD format."""
    if val is None:
        return None
    if isinstance(val, datetime):
        return val.strftime("%Y-%m-%d")

    raw_str = str(val).strip()
    if not raw_str:
        return None

    # Handle multi-line entries (e.g. for multi-org entries)
    if "{{ORG_NAME}}:" in raw_str:
        match = re.search(r"{{ORG_NAME}}:\s*(\d{4}[.-]\d{2}[.-]\d{2})", raw_str)
        if match:
            return match.group(1).replace(".", "-")

    # Match standard date patterns YYYY.MM.DD or YY.MM.DD
    match = re.search(r"(\d{2,4})[.-](\d{1,2})(?:[.-](\d{1,2}))?", raw_str)
    if match:
        year, month_str, day_str = match.groups()
        if len(year) == 2:
            year = "20" + year
        month = int(month_str)
        day = int(day_str) if day_str else 1
        return f"{year}-{month:02d}-{day:02d}"

    return raw_str


def parse_degree_major(val: Any) -> tuple[Optional[str], Optional[str]]:
    """Parse degree and major from string like '2026/박사(데이터사이언스학)'."""
    if not val:
        return None, None
    raw_str = str(val).strip()
    match = re.search(r"(?:[\d/]+)?\s*([가-힇a-zA-Z]+)(?:\(([^)]+)\))?", raw_str)
    if match:
        degree = match.group(1)
        major = match.group(2)
        return degree, major
    return None, None


def parse_department(val: Any) -> tuple[Optional[str], Optional[str]]:
    """Parse department string into division and team."""
    if not val:
        return None, None
    raw_str = str(val).strip()
    if any(keyword in raw_str for keyword in ["본부", "사업부", "총괄", "직속", "리엔"]):
        return raw_str, None
    if "팀" in raw_str:
        return None, raw_str
    return raw_str, None


class HRCollector:
    """Collector for HR personnel records and project allocation percentages."""

    name: str = "hr"

    def __init__(self, excel_path: Optional[str] = None):
        self.excel_path = excel_path or DEFAULT_HR_EXCEL_PATH

    def collect(self, ctx: RunContext) -> Iterator[dict[str, Any]]:
        """Collect person and project allocation records from HR excel workbook."""
        if not os.path.exists(self.excel_path):
            ctx.logger.error(f"HR source excel file not found: {self.excel_path}")
            return

        ctx.logger.info(f"Loading HR Excel source from: {self.excel_path}")
        wb = openpyxl.load_workbook(self.excel_path, data_only=True)
        if "인력별 상세정보" not in wb.sheetnames:
            ctx.logger.error("Sheet '인력별 상세정보' not found in HR excel workbook.")
            return

        sheet = wb["인력별 상세정보"]

        # Read task headers from Row 3
        task_headers = [sheet.cell(row=3, column=col).value for col in TASK_COLUMNS]

        udl_person_index = 1
        ext_signal_index = 1

        for row_idx in range(4, sheet.max_row + 1):
            name_val = sheet.cell(row=row_idx, column=COL_NAME).value
            if not name_val or not str(name_val).strip():
                continue

            name = str(name_val).strip()
            org = str(sheet.cell(row=row_idx, column=COL_ORG).value or "").strip()

            # Parse project allocations (only non-zero numbers)
            allocations: dict[str, float] = {}
            for col_idx, task_name in zip(TASK_COLUMNS, task_headers):
                cell_val = sheet.cell(row=row_idx, column=col_idx).value
                if cell_val is not None and str(cell_val).strip() not in ("", "0"):
                    try:
                        allocations[str(task_name)] = float(cell_val)
                    except (ValueError, TypeError) as err:
                        ctx.logger.warning(
                            f"Row {row_idx}: Failed to convert allocation value for {name} ({task_name}): {err}"
                        )

            # Filter target: Urban Data Lab personnel (including '{{ORG_NAME}}', '모두', '퇴사')
            if org in ("{{ORG_NAME}}", "모두", "퇴사"):
                person_id = f"UDL-{udl_person_index:03d}"
                udl_person_index += 1

                dept_val = sheet.cell(row=row_idx, column=COL_DEPT).value
                division, team = parse_department(dept_val)
                position = sheet.cell(row=row_idx, column=COL_POSITION).value
                if position:
                    position = str(position).strip()

                degree_raw = sheet.cell(row=row_idx, column=COL_DEGREE_MAJOR).value
                degree, major = parse_degree_major(degree_raw)

                join_raw = sheet.cell(row=row_idx, column=COL_JOIN_DATE).value
                resign_raw = sheet.cell(row=row_idx, column=COL_RESIGN_DATE).value

                join_date = parse_date(join_raw)
                resign_date = parse_date(resign_raw)

                # Determine employment_status
                if org == "퇴사" or resign_date is not None or (resign_raw and str(resign_raw).strip()):
                    employment_status = "resigned"
                else:
                    employment_status = "active"

                payload = {
                    "person_id": person_id,
                    "name": name,
                    "organization": "{{ORG_NAME}}",
                    "division": division,
                    "team": team,
                    "position": position,
                    "employment_status": employment_status,
                    "join_date": join_date,
                    "resign_date": resign_date,
                    "degree": degree,
                    "major": major,
                    "email": None,
                    "project_allocations": allocations,
                }

                source_info = {
                    "connector": "manual",
                    "root": "drive",
                    "path": RELATIVE_SOURCE_PATH,
                }

                # Roy 2026-08-04: the NAVER Works directory is the personnel master
                # (DirectoryCollector). This spreadsheet is a 2026-07-22 export whose
                # org column had already drifted, so it no longer defines who works
                # here -- it contributes project allocation only, and only for people
                # the directory still lists. Past leavers are not carried over.
                if not allocations:
                    continue
                allocation_payload = {
                    "name": payload["name"],
                    "source_person_id": person_id,
                    "project_allocations": allocations,
                }
                record = make_record(
                    record_id=f"alloc:{person_id}",
                    record_type="signal",
                    payload=allocation_payload,
                    source=source_info,
                    provenance="observed",
                    status="present",
                    hash_basis="fields",
                )
                yield record

            else:
                # External organization personnel with project allocations
                if allocations:
                    signal_id = f"SIG-EXT-ALLOC-{ext_signal_index:03d}"
                    ext_signal_index += 1

                    payload = {
                        "external_person_name": name,
                        "organization": org,
                        "project_allocations": allocations,
                    }

                    source_info = {
                        "connector": "manual",
                        "root": "drive",
                        "path": RELATIVE_SOURCE_PATH,
                    }

                    record = make_record(
                        record_id=f"sig:{signal_id}",
                        record_type="signal",
                        payload=payload,
                        source=source_info,
                        provenance="ledger",
                        status="present",
                        hash_basis="fields",
                    )
                    yield record
