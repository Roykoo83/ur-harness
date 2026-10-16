"""Dual PID-file and directory locking."""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from types import TracebackType

from .timeutil import iso_now


class DualLock:
    """Acquire both independent locks or neither."""

    def __init__(
        self,
        pid_path: str | Path,
        directory_path: str | Path,
        logger: logging.Logger | None = None,
    ) -> None:
        self.pid_path = Path(pid_path)
        self.directory_path = Path(directory_path)
        self.logger = logger or logging.getLogger("rpbd.lock")
        self.acquired = False
        self._pid_created = False
        self._dir_created = False

    def _holder(self) -> tuple[str, str]:
        try:
            data = json.loads(self.pid_path.read_text(encoding="utf-8"))
            return str(data.get("pid", "unknown")), str(data.get("since", "unknown"))
        except (OSError, ValueError, TypeError):
            return "unknown", "unknown"

    def acquire(self) -> bool:
        if self.acquired:
            return True
        since = iso_now()
        try:
            self.pid_path.parent.mkdir(parents=True, exist_ok=True)
            fd = os.open(self.pid_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
            self._pid_created = True
            with os.fdopen(fd, "w", encoding="utf-8") as stream:
                json.dump({"pid": os.getpid(), "since": since}, stream)
                stream.write("\n")
            self.directory_path.mkdir(parents=False, exist_ok=False)
            self._dir_created = True
        except OSError:
            pid, held_since = self._holder()
            self.logger.warning("SKIP: lock held by pid=%s since=%s", pid, held_since)
            self.release()
            return False
        self.acquired = True
        return True

    def release(self) -> None:
        if self._dir_created:
            try:
                self.directory_path.rmdir()
            except OSError:
                pass
        if self._pid_created:
            try:
                self.pid_path.unlink()
            except OSError:
                pass
        self._dir_created = self._pid_created = self.acquired = False

    def __enter__(self) -> "DualLock":
        self.acquire()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.release()


def acquire_daemon_lock(
    data_dir: str | Path,
    *,
    pid_path: str | Path | None = None,
    logger: logging.Logger | None = None,
) -> DualLock:
    """Build and acquire the C5.6 daemon lock."""
    lock = DualLock(
        pid_path or Path.home() / ".rpbd" / "daemon.pid",
        Path(data_dir) / ".lock",
        logger,
    )
    lock.acquire()
    return lock
