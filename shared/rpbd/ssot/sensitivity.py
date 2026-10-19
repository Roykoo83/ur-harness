"""C1.7 sensitivity classification.

Guards the L1 document layer, which previously had no sensitivity control:
exclusion_list.md targets vault migration, C3.6 targets ontology notes, and
graph/verify.py targets graph load. Nothing covered plain document records.

The stakes are asymmetric. Records are append-only, so a body extracted once
cannot be removed. Over-blocking costs a lookup; under-blocking writes payroll
and bank details into the ledger permanently. Classify conservatively.
"""

from __future__ import annotations

import re
from typing import Literal

Sensitivity = Literal["high", "medium", "normal"]

# Filename markers for documents whose body must never be read.
#
# 2026-09-22: bare "인건비" left this list (운영자: "메일로 공유되는 인건비는 비밀이 아니야.
# 그냥 필터링 하지마"). It had graded RCMS manual screenshots, a budget-approval mail and a
# public subsidy guideline as S3. The split that replaces it is below: a *record* of who was
# paid stays high; a *procedure* for paying does not.
_HIGH_MARKERS = (
    "급여", "통장", "계좌", "카드", "주민",
    "원천징수", "건강보험", "이력서", "연봉", "자격득실",
    "가족관계", "재직증명", "경력증명", "신분증", "여권", "등본", "초본",
)

# Labour cost is only sensitive as a *record*. "인건비 지급내역.xlsx" lists what each person
# was paid; "인건비 집행 방법.pdf" explains how to file it. The first must not leak, the
# second is a manual. Neither carries a person's name in the filename, so _MONEY_TERMS +
# _has_person_name cannot tell them apart — this pair does.
_LABOUR_COST = "인건비"
_RECORD_TERMS = ("지급내역", "지급명세", "명세", "대장", "집행내역", "산정내역",
                 "지급조서", "내역서", "정산내역")

# Folders whose contents are sensitive regardless of the individual filename.
# Observed in production: 03.행정/인건비/5월/신분증_<이름>.jpg reads as harmless by
# name alone. The directory is the stronger signal -- what a file sits next to
# says more about it than what it is called.
# 2026-09-22: "인건비" stays HERE while it leaves _HIGH_MARKERS. The distinction is the
# whole point of 운영자's decision: a *document about* labour cost (mail, RCMS manual, budget
# request) is ordinary work; a *folder* named 인건비 under 03.행정 holds the payslips and the
# ID scans that sit beside them. Path is the stronger signal, filename the weaker one.
_HIGH_PATH_MARKERS = ("인건비", "급여", "통장", "계좌", "증빙서류/인건비")

# Money terms that are harmless alone but not next to a person's name.
# "자문비 지급 동의서_박OO.pdf" passed every earlier rule: no high marker, no
# payroll folder. A document tying a named individual to money is the exact
# thing that must never surface, so the pair is what gets graded, not either half.
_MONEY_TERMS = (
    "지급", "정산", "자문비", "수당", "사례비", "강사료", "원고료",
    "용역비", "지원금", "환급", "청구", "실비", "여비", "출장비",
)

# Categories that concentrate personnel-cost evidence.
_MEDIUM_CATEGORIES = frozenset({"budget_exec"})
_MEDIUM_MARKERS = ("계약", "협약", "정산")

# A Korean personal name next to a separator, e.g. "홍길동_" or "홍길동 (".
# Anchored on a surname list -- matching "any Korean word + space" flagged
# ordinary titles like "사용법 안내" and made the whole grade meaningless.
_SURNAMES = (
    "김이박최정강조윤장임한오서신권황안송류전홍고문양손배백허유남심"
    "노하곽성차주우구나민진지엄채원천방공현함변염여추도소석선설마길"
    "연위표명기반왕금옥육인맹제모탁국어은편용"
)

# A delimited token starting with a surname is not yet a name. Measured on 7,038
# production filenames: the surname pattern alone matched 268 distinct tokens and
# only ~25 were people, so 1,419 ordinary documents were graded medium. The two
# filters below carry that 268 down to ~35.
#
# Length is the first filter. Every two-character hit in that corpus was a common
# noun -- 지원(300 files), 전환(261), 어반(50), 공문(43) -- while every real name
# was three. Surname + two syllables is the shape we accept.
_NAME_LENGTH = 3

# Second filter: 조사 and 접미사 that close a compound noun but never a given
# name. Without it 안내서, 연락처, 변경후, 기반의 all read as people. Each ending
# here was checked against the real names in the same corpus; where an ending
# could plausibly close a name (성·기·지·국·림, as in {{MEMBER_C}}·이중기·하성지·정성국
# ·현창림) it is deliberately left out, so those stay names and ambiguity still
# resolves upward.
_NOT_A_NAME_ENDINGS = frozenset(
    "의을를에과및이해부사대경말문"  # 조사·연결어미·일반명사 꼬리: 기반의, 강화를, 제품이, 인사말, 공고문
    "서본용후팀처별회장안청품명법실형금비세체소암라스뷰팅폼갭막률향가일전인화도"
    # 접미사·단위어: 안내서, 제출본, 공유용, 변경후, 연구팀, 연락처, 기관별, 조직도
)

# Third filter, and the only way a name of another length is recognised: a title
# right after it. 오별(2) and 장해누리(4) are real staff names that the length
# rule cannot see, and an honorific is strong enough to override it.
_TITLES = ("님", "씨", "선생", "책임", "팀장", "부장", "과장", "교수", "박사")

# Titles are nouns too. "박사님"·"소장님" name a role, not a person, and
# "국립보건연구원" is an institute -- so the word carrying the title is checked
# against a closed list of 직위명 rather than the open set of Korean nouns.
_TITLE_WORDS = frozenset(
    {
        "박사", "소장", "대표", "이사", "부장", "과장", "팀장", "선생", "교수",
        "책임", "연구원", "회장", "사장", "국장", "실장", "원장", "위원", "담당",
    }
)

# The trailing group is optional; `.` closes the token so that the commonest
# attribution slot of all -- "..._홍길동.pdf" -- is seen. The core is lazy so a
# title stays out of it: greedy, "박사님" parses as the three-syllable core
# "박사님" with no title and slips past the 직위명 guard below.
_NAME_PATTERN = re.compile(
    rf"(?:^|[_ ()\-\[])(?P<core>[{_SURNAMES}][가-힣]{{1,3}}?)"
    rf"\s*(?P<title>{"|".join(_TITLES)})?(?=[_ ()\-\].]|$)"
)


def _has_person_name(text: str) -> bool:
    """True when the filename carries something that is actually a person's name."""
    for match in _NAME_PATTERN.finditer(text):
        core = match.group("core")
        if core in _TITLE_WORDS:
            continue
        if match.group("title"):
            return True
        if len(core) == _NAME_LENGTH and core[-1] not in _NOT_A_NAME_ENDINGS:
            return True
    return False

# Patterns checked against *extracted text* as a second line of defence.
_RRN = re.compile(r"\b\d{6}\s*[-–]\s*[1-4]\d{6}\b")
_ACCOUNT = re.compile(r"\b\d{2,6}[- ]\d{2,6}[- ]\d{2,7}\b")
_CARD = re.compile(r"\b(?:\d{4}[- ]){3}\d{4}\b")


def classify(
    name: str, category: str | None = None, rel_path: str | None = None
) -> Sensitivity:
    """Classify by filename, containing folder and category. Ambiguity resolves upward."""
    lowered = name or ""
    path = rel_path or ""
    if any(marker in path for marker in _HIGH_PATH_MARKERS):
        return "high"
    if any(marker in lowered for marker in _HIGH_MARKERS):
        return "high"
    if _LABOUR_COST in lowered and any(t in lowered for t in _RECORD_TERMS):
        return "high"
    named = _has_person_name(lowered)
    if named and any(term in lowered for term in _MONEY_TERMS):
        return "high"
    if category in _MEDIUM_CATEGORIES:
        return "medium"
    if any(marker in lowered for marker in _MEDIUM_MARKERS):
        return "medium"
    if named:
        return "medium"
    return "normal"


def may_extract_body(sensitivity: Sensitivity) -> bool:
    """High-sensitivity documents are never opened, not even to discard the result."""
    return sensitivity != "high"


def text_contains_secrets(text: str) -> list[str]:
    """Second-line check on extracted text. Returns the kinds found, empty if clean."""
    found: list[str] = []
    if _RRN.search(text):
        found.append("resident_registration_number")
    # A card number also satisfies the looser account shape; report the specific
    # kind and strip it before testing for accounts so one secret is not counted twice.
    remainder = text
    if _CARD.search(remainder):
        found.append("card_number")
        remainder = _CARD.sub(" ", remainder)
    if _ACCOUNT.search(remainder):
        found.append("account_number")
    return found


def mask_name(name: str, sensitivity: Sensitivity, category_label: str | None = None) -> str:
    """Filename shown in view models. High-sensitivity names never reach the browser."""
    if sensitivity != "high":
        return name
    label = category_label or "문서"
    return f"[민감 문서] {label}"


__all__ = [
    "Sensitivity",
    "classify",
    "may_extract_body",
    "text_contains_secrets",
    "mask_name",
]
