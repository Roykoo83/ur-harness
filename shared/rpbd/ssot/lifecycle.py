"""Project lifecycle validation, migration, and transition suggestions."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from enum import Enum
from typing import Any


class ProjectType(str, Enum):
    RND = "rnd"
    ETC = "etc"
    RA = "ra"


class RNDStage(str, Enum):
    PROPOSAL = "제안"
    AWAITING_AWARD = "수주대기"
    LOST = "실주"
    AWARDED = "수주"
    AGREEMENT = "협약"
    EXECUTION = "수행"
    CLOSED = "종료"
    BUDGET_CLOSED = "예산마감"
    WRAPUP = "Wrapup"


class EtcStage(str, Enum):
    DISCUSSION = "협의"
    EXECUTION = "수행"
    RESULT = "결과"
    WRAPUP = "Wrapup"


# Acronym-style aliases keep imports readable for either naming convention.
ETCStage = EtcStage
RndStage = RNDStage


_ALLOWED_STAGES: dict[ProjectType, tuple[str, ...]] = {
    ProjectType.RND: tuple(stage.value for stage in RNDStage),
    ProjectType.ETC: tuple(stage.value for stage in EtcStage),
    ProjectType.RA: (),
}


def _project_type(value: ProjectType | str) -> ProjectType:
    try:
        return value if isinstance(value, ProjectType) else ProjectType(value)
    except ValueError as exc:
        raise ValueError(f"unsupported project_type: {value!r}") from exc


def _stage_value(value: str | Enum | None) -> str | None:
    if value is None:
        return None
    return str(value.value) if isinstance(value, Enum) else str(value)


def allowed_stages(project_type: ProjectType | str) -> tuple[str, ...]:
    """Return the stages defined for a project type."""
    return _ALLOWED_STAGES[_project_type(project_type)]


def is_stage_allowed(project_type: ProjectType | str, stage: str | Enum | None) -> bool:
    """Return whether a stage belongs to the given project type."""
    kind = _project_type(project_type)
    value = _stage_value(stage)
    if value is None:
        return kind is ProjectType.RA
    return value in _ALLOWED_STAGES[kind]


def validate_stage(project_type: ProjectType | str, stage: str | Enum | None) -> str | None:
    """Validate and normalize a stage, raising on a type mismatch."""
    kind = _project_type(project_type)
    value = _stage_value(stage)
    if not is_stage_allowed(kind, value):
        expected = ", ".join(_ALLOWED_STAGES[kind]) or "no stage (design pending)"
        raise ValueError(
            f"stage {value!r} is not allowed for project_type {kind.value!r}; "
            f"expected {expected}"
        )
    return value


@dataclass(frozen=True, slots=True)
class TransitionResult:
    stage: str
    stage_updated: str
    stage_history: list[dict[str, Any]]

    def __getitem__(self, key: str) -> Any:
        return getattr(self, key)


def transition_stage(
    project_type: ProjectType | str,
    current_stage: str | Enum,
    new_stage: str | Enum,
    *,
    entered_at: str,
    by: str,
    note: str | None = None,
    stage_history: list[dict[str, Any]] | None = None,
) -> TransitionResult:
    """Record a human-approved transition without restricting skips or reversals."""
    validate_stage(project_type, current_stage)
    normalized = validate_stage(project_type, new_stage)
    if normalized is None:
        raise ValueError("a transition target must have a stage")
    history = stage_history if stage_history is not None else []
    history.append(
        {
            "stage": normalized,
            "entered_at": entered_at,
            "by": by,
            "note": note,
        }
    )
    return TransitionResult(
        stage=normalized,
        stage_updated=entered_at,
        stage_history=history,
    )


record_transition = transition_stage


@dataclass(frozen=True, slots=True)
class PhaseMappingResult:
    project_type: str
    stage: str | None
    needs_confirmation: bool

    def __getitem__(self, key: str) -> Any:
        return getattr(self, key)


def _as_date(value: date | datetime | str) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(value[:10])
    except (TypeError, ValueError) as exc:
        raise ValueError(f"expected an ISO date, got {value!r}") from exc


def map_phase_to_stage(
    phase: str,
    *,
    deadline_passed: bool | None = None,
    deadline: date | datetime | str | None = None,
    as_of: date | datetime | str | None = None,
    settlement_complete: bool | None = None,
) -> PhaseMappingResult:
    """Map a legacy phase without inventing an uncertain detailed stage."""
    if phase == "applying":
        if deadline_passed is None and deadline is not None:
            reference_date = _as_date(as_of) if as_of is not None else date.today()
            deadline_passed = _as_date(deadline) < reference_date
        if deadline_passed is None:
            return PhaseMappingResult(ProjectType.RND.value, None, True)
        stage = RNDStage.AWAITING_AWARD if deadline_passed else RNDStage.PROPOSAL
        return PhaseMappingResult(ProjectType.RND.value, stage.value, False)
    if phase == "active":
        return PhaseMappingResult(ProjectType.RND.value, RNDStage.EXECUTION.value, False)
    if phase == "closed":
        if settlement_complete is None:
            return PhaseMappingResult(ProjectType.RND.value, None, True)
        stage = RNDStage.BUDGET_CLOSED if settlement_complete else RNDStage.CLOSED
        return PhaseMappingResult(ProjectType.RND.value, stage.value, False)
    raise ValueError(f"unsupported legacy phase: {phase!r}")


@dataclass(frozen=True, slots=True)
class TransitionCandidate:
    from_stage: str
    to_stage: str
    reason: str
    needs_confirmation: bool = True

    def __getitem__(self, key: str) -> Any:
        return getattr(self, key)


def detect_transition_candidates(
    project_type: ProjectType | str,
    current_stage: str | Enum | None,
    *,
    period_end: date | datetime | str | None = None,
    as_of: date | datetime | str | None = None,
) -> list[TransitionCandidate]:
    """Detect possible transitions; this function never changes project state."""
    kind = _project_type(project_type)
    current = validate_stage(kind, current_stage)
    if (
        kind is ProjectType.RND
        and current == RNDStage.EXECUTION.value
        and period_end is not None
    ):
        reference_date = _as_date(as_of) if as_of is not None else date.today()
        if _as_date(period_end) < reference_date:
            return [
                TransitionCandidate(
                    from_stage=RNDStage.EXECUTION.value,
                    to_stage=RNDStage.CLOSED.value,
                    reason="project period end date has passed",
                )
            ]
    return []
