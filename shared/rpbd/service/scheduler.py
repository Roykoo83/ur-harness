"""Small cron-like scheduler for weekday service jobs."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, time, timedelta
import logging
from typing import Callable, Iterable


def next_run_time(
    after: datetime,
    *,
    hour: int = 8,
    minute: int = 30,
    weekdays: Iterable[int] = range(5),
) -> datetime:
    """Return the first scheduled datetime strictly after ``after``.

    Weekdays use ``datetime.weekday`` numbering where Monday is zero. The
    returned value preserves the input timezone and fold behavior.
    """
    if not 0 <= hour <= 23:
        raise ValueError("hour must be between 0 and 23")
    if not 0 <= minute <= 59:
        raise ValueError("minute must be between 0 and 59")
    selected = frozenset(weekdays)
    if not selected or any(day not in range(7) for day in selected):
        raise ValueError("weekdays must contain values between 0 and 6")

    for offset in range(8):
        day = after.date() + timedelta(days=offset)
        if day.weekday() not in selected:
            continue
        candidate = datetime.combine(
            day,
            time(hour=hour, minute=minute, tzinfo=after.tzinfo),
        )
        if candidate > after:
            return candidate
    raise RuntimeError("unable to calculate the next run")


@dataclass(frozen=True)
class CronSchedule:
    hour: int = 8
    minute: int = 30
    weekdays: tuple[int, ...] = (0, 1, 2, 3, 4)

    def next_after(self, after: datetime) -> datetime:
        return next_run_time(
            after,
            hour=self.hour,
            minute=self.minute,
            weekdays=self.weekdays,
        )


@dataclass
class ScheduledJob:
    name: str
    callback: Callable[[], object]
    schedule: CronSchedule
    next_at: datetime


@dataclass
class CronScheduler:
    """In-process scheduler whose clock is supplied by the caller."""

    logger: logging.Logger = field(default_factory=lambda: logging.getLogger("rpbd.scheduler"))
    jobs: list[ScheduledJob] = field(default_factory=list)

    def add_job(
        self,
        name: str,
        callback: Callable[[], object],
        schedule: CronSchedule,
        *,
        now: datetime,
    ) -> ScheduledJob:
        job = ScheduledJob(name, callback, schedule, schedule.next_after(now))
        self.jobs.append(job)
        return job

    def run_due(self, now: datetime) -> list[str]:
        """Run all due jobs once and return their names."""
        completed: list[str] = []
        for job in self.jobs:
            if job.next_at > now:
                continue
            try:
                job.callback()
            except Exception:
                self.logger.exception("scheduled job failed: %s", job.name)
            completed.append(job.name)
            job.next_at = job.schedule.next_after(now)
        return completed

    def seconds_until_next(self, now: datetime, maximum: float = 30.0) -> float:
        if not self.jobs:
            return maximum
        delay = min((job.next_at - now).total_seconds() for job in self.jobs)
        return max(0.0, min(maximum, delay))
