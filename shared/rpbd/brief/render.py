"""Render daily brief data in the established RPBD Markdown layout."""

from __future__ import annotations

from collections import Counter

from rpbd.detect import Finding

from .daily import DailyBrief

_SECTIONS = (
    ("urgent", "## 🔴 긴급"),
    ("warn", "## 🟡 주의"),
    ("check", "## 🟢 점검"),
)


def _finding_line(brief: DailyBrief, finding: Finding) -> list[str]:
    subject = brief.project_names.get(finding.project_id or "", finding.project_id or "공통")
    detail = finding.detail
    if finding.action:
        detail = f"{detail} {finding.action}"
    lines = [f"- **[{subject}]** **{finding.title}** — {detail}"]
    lines.append(f"  - 근거: {', '.join(finding.evidence)}")
    return lines


def _application_line(brief: DailyBrief, index: int) -> str:
    item = brief.applications[index]
    if item.due is None:
        timing = "마감 확인 필요"
    else:
        days_left = (item.due - brief.generated_at.date()).days
        if days_left < 0:
            timing = f"마감 {item.due.isoformat()} (지났음, 결과 대기)"
        else:
            timing = f"마감 {item.due.isoformat()} (D-{days_left})"
    ministry = f" · {item.ministry}" if item.ministry else ""
    return f"- **{item.name}** — {timing}{ministry}"


def render_markdown(brief: DailyBrief) -> str:
    main = brief.main_findings
    counts = Counter(item.severity for item in main)
    lines = [
        f"# 연구기획사업부 데일리 브리프 — {brief.generated_at:%Y-%m-%d}",
        "",
        f"> 자동 생성 {brief.generated_at:%H:%M} · 원천: {brief.source_summary}",
        "",
        (
            f"수행중 {brief.active_count}건 · 지원중 {brief.applying_count}건 · "
            f"오늘의 항목 {len(main)}건 "
            f"(긴급 {counts['urgent']} · 주의 {counts['warn']} · 점검 {counts['check']})"
        ),
        "",
    ]
    for severity, heading in _SECTIONS:
        lines.extend((heading, ""))
        items = [item for item in main if item.severity == severity]
        if items:
            for item in items:
                lines.extend(_finding_line(brief, item))
        else:
            lines.append("- 해당 없음")
        lines.append("")

    lines.extend(("## 📨 지원중 파이프라인 (제안·수주)", ""))
    if brief.applications:
        lines.extend(_application_line(brief, index) for index in range(len(brief.applications)))
    else:
        lines.append("- 해당 없음")
    lines.extend(
        (
            "",
            "---",
            "SSOT record_id/event_id는 각 항목의 근거에서 확인할 수 있습니다.",
            "",
        )
    )
    return "\n".join(lines)


render = render_markdown

__all__ = ["render", "render_markdown"]
