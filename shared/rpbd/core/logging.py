"""Logging setup without import-time configuration."""

from __future__ import annotations

import logging


def configure_logging(level: int | str = logging.INFO) -> logging.Logger:
    """Configure and return the RPBD logger."""
    logger = logging.getLogger("rpbd")
    if not logger.handlers:
        handler = logging.StreamHandler()
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
        logger.addHandler(handler)
    logger.setLevel(level)
    return logger


def get_logger(name: str | None = None) -> logging.Logger:
    return logging.getLogger("rpbd" if name is None else f"rpbd.{name}")
