"""Config loader for NAVER WORKS agent.

Secrets live only in ~/.nworks/ (0600). Layout:
  ~/.nworks/config.json      {client_id, client_secret, service_account, private_key_path, bot_id?, domain_id?}
  ~/.nworks/private_YYYYMMDD.key   RSA private key downloaded from Developer Console
  ~/.nworks/token_cache.json  (managed by auth.py)
"""
from __future__ import annotations

import json
import os
import stat
from pathlib import Path

NWORKS_DIR = Path.home() / ".nworks"
CONFIG_PATH = NWORKS_DIR / "config.json"

REQUIRED = ("client_id", "client_secret", "service_account", "private_key_path")


class ConfigError(RuntimeError):
    pass


def load_config() -> dict:
    if not CONFIG_PATH.exists():
        raise ConfigError(
            f"{CONFIG_PATH} 없음 — P0 앱 등록 후 config.example.json을 참고해 생성하세요")
    # Windows 는 POSIX 권한 비트가 없어 st_mode 가 늘 0o666 으로 보인다 — 접근 제한은 ACL(icacls)로 한다
    if os.name != "nt":
        mode = stat.S_IMODE(CONFIG_PATH.stat().st_mode)
        if mode & 0o077:
            raise ConfigError(f"{CONFIG_PATH} 권한이 {oct(mode)} — chmod 600 필요")
    cfg = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    missing = [k for k in REQUIRED if not cfg.get(k)]
    if missing:
        raise ConfigError(f"config.json 누락 필드: {missing}")
    key_path = Path(os.path.expanduser(cfg["private_key_path"]))
    if not key_path.exists():
        raise ConfigError(f"private key 없음: {key_path}")
    cfg["private_key_path"] = str(key_path)
    return cfg
