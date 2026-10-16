"""Local configuration loading for RPBD services."""

from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from typing import Any, Mapping
import tomllib


DEFAULT_CONFIG: dict[str, Any] = {
    "daemon": {
        "poll_seconds": 30.0,
    },
    "pipeline": {
        "dry_run": False,
    },
    "schedule": {
        "weekdays": [0, 1, 2, 3, 4],
        "hour": 8,
        "minute": 30,
    },
    "notify": {
        "enabled": False,
        "channels": [],
        "dry_run": True,
    },
}


def _merge(base: dict[str, Any], override: Mapping[str, Any]) -> dict[str, Any]:
    for key, value in override.items():
        if isinstance(value, Mapping) and isinstance(base.get(key), dict):
            base[key] = _merge(dict(base[key]), value)
        else:
            base[key] = deepcopy(value)
    return base


def load_config(path: str | Path | None = None) -> dict[str, Any]:
    """Load TOML configuration over safe defaults.

    A missing file is expected on first use. Malformed TOML is allowed to
    raise so configuration errors cannot be mistaken for a valid setup.
    """
    config_path = Path(path).expanduser() if path is not None else Path.home() / ".rpbd" / "config.toml"
    config = deepcopy(DEFAULT_CONFIG)
    try:
        with config_path.open("rb") as stream:
            loaded = tomllib.load(stream)
    except FileNotFoundError:
        return config
    return _merge(config, loaded)
