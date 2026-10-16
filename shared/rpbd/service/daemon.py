"""RPBD service daemon and lock-aware one-shot runner."""

from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
import json
import logging
from pathlib import Path
import signal
import threading
from types import FrameType
from typing import Any, Callable, Mapping

from rpbd.core.config import load_config
from rpbd.core.lock import DualLock, acquire_daemon_lock
from rpbd.core.logging import configure_logging
from rpbd.service.scheduler import CronSchedule, CronScheduler


KST = timezone(timedelta(hours=9))
PROJECT_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_DATA_DIR = PROJECT_ROOT / "data"
VALID_INVOCATIONS = frozenset({"watcher", "manual", "cron"})


class RPBDDaemon:
    """Own the process lock and invoke the existing integration pipeline."""

    def __init__(
        self,
        *,
        config: Mapping[str, Any] | None = None,
        data_dir: str | Path = DEFAULT_DATA_DIR,
        pid_path: str | Path | None = None,
        logger: logging.Logger | None = None,
        pipeline_runner: Callable[..., dict[str, Any]] | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self.config = dict(config) if config is not None else load_config()
        self.data_dir = Path(data_dir)
        self.pid_path = Path(pid_path) if pid_path is not None else None
        self.logger = logger or logging.getLogger("rpbd.daemon")
        self.pipeline_runner = pipeline_runner
        self.clock = clock or (lambda: datetime.now(KST))
        self.stop_event = threading.Event()
        self.lock: DualLock | None = None
        self.scheduler = CronScheduler(self.logger)
        self._previous_signals: dict[int, Any] = {}

    def acquire_lock(self) -> bool:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.lock = acquire_daemon_lock(
            self.data_dir,
            pid_path=self.pid_path,
            logger=self.logger,
        )
        return self.lock.acquired

    def release_lock(self) -> None:
        if self.lock is not None:
            self.lock.release()
            self.lock = None

    def request_stop(self, signum: int | None = None, frame: FrameType | None = None) -> None:
        if signum is not None:
            self.logger.info("stop requested by signal=%s", signum)
        self.stop_event.set()

    def _install_signal_handlers(self) -> None:
        for signum in (signal.SIGTERM, signal.SIGINT):
            self._previous_signals[signum] = signal.getsignal(signum)
            signal.signal(signum, self.request_stop)

    def _restore_signal_handlers(self) -> None:
        for signum, previous in self._previous_signals.items():
            signal.signal(signum, previous)
        self._previous_signals.clear()

    def _execute(self, invoked_by: str) -> dict[str, Any]:
        if invoked_by not in VALID_INVOCATIONS:
            raise ValueError(f"invalid invocation source: {invoked_by}")
        dry_run = bool(self.config.get("pipeline", {}).get("dry_run", False))
        runner = self.pipeline_runner
        if runner is None:
            from rpbd.pipeline import run as runner

        manifest = runner(dry_run=dry_run, invoked_by=invoked_by)
        if not manifest.get("run_id"):
            raise RuntimeError("pipeline manifest is missing run_id")
        manifest["invoked_by"] = invoked_by
        self.logger.info("run completed: run_id=%s invoked_by=%s", manifest["run_id"], invoked_by)
        return manifest

    def run_once(self, invoked_by: str = "manual") -> dict[str, Any] | None:
        owns_lock = self.lock is None or not self.lock.acquired
        if owns_lock and not self.acquire_lock():
            return None
        try:
            return self._execute(invoked_by)
        finally:
            if owns_lock:
                self.release_lock()

    def run_forever(self, *, install_signal_handlers: bool = True) -> int:
        if not self.acquire_lock():
            return 0
        try:
            schedule_config = self.config.get("schedule", {})
            schedule = CronSchedule(
                hour=int(schedule_config.get("hour", 8)),
                minute=int(schedule_config.get("minute", 30)),
                weekdays=tuple(schedule_config.get("weekdays", range(5))),
            )
            poll_seconds = float(self.config.get("daemon", {}).get("poll_seconds", 30.0))
            self.scheduler.add_job("daily-pipeline", lambda: self._execute("cron"), schedule, now=self.clock())
            if install_signal_handlers:
                self._install_signal_handlers()
            while not self.stop_event.is_set():
                now = self.clock()
                self.scheduler.run_due(now)
                self.stop_event.wait(self.scheduler.seconds_until_next(now, poll_seconds))
        finally:
            if install_signal_handlers:
                self._restore_signal_handlers()
            self.release_lock()
        return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run the RPBD service daemon.")
    parser.add_argument("--once", action="store_true", help="run the pipeline once and exit")
    parser.add_argument("--invoked-by", choices=sorted(VALID_INVOCATIONS), default="manual")
    args = parser.parse_args(argv)
    logger = configure_logging()
    daemon = RPBDDaemon(logger=logger)
    if args.once:
        manifest = daemon.run_once(args.invoked_by)
        if manifest is not None:
            print(json.dumps(manifest, ensure_ascii=False, indent=2))
        return 0
    return daemon.run_forever()


if __name__ == "__main__":
    raise SystemExit(main())
