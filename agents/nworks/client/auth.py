"""Service Account (JWT bearer, RFC 7523) token management.

Flow per api_survey.md: build RS256-signed JWT (exp <= 60min), exchange at
TOKEN_URL with grant_type=urn:ietf:params:oauth:grant-type:jwt-bearer.
Access token cached to ~/.nworks/token_cache.json until near expiry.
User Account OAuth (mail/drive, P3) is intentionally NOT implemented yet.
"""
from __future__ import annotations

import json
import time
from pathlib import Path

import jwt  # PyJWT
import requests

from .config import NWORKS_DIR, load_config
from .endpoints import TOKEN_URL

CACHE_PATH = NWORKS_DIR / "token_cache.json"
SKEW = 120  # refresh this many seconds before expiry


def _build_assertion(cfg: dict) -> str:
    now = int(time.time())
    payload = {
        "iss": cfg["client_id"],
        "sub": cfg["service_account"],
        "iat": now,
        "exp": now + 3600,  # docs: JWT exp max 60min
    }
    key = Path(cfg["private_key_path"]).read_text()
    return jwt.encode(payload, key, algorithm="RS256")


def get_token(scopes: list[str]) -> str:
    """Return a valid access token for the given scopes (cached)."""
    scope_str = " ".join(sorted(set(scopes)))
    if CACHE_PATH.exists():
        cache = json.loads(CACHE_PATH.read_text())
        ent = cache.get(scope_str)
        if ent and ent["expires_at"] - SKEW > time.time():
            return ent["access_token"]
    else:
        cache = {}

    cfg = load_config()
    resp = requests.post(TOKEN_URL, data={
        "assertion": _build_assertion(cfg),
        "grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer",
        "client_id": cfg["client_id"],
        "client_secret": cfg["client_secret"],
        "scope": scope_str,
    }, timeout=15)
    if resp.status_code != 200:
        raise RuntimeError(f"token 발급 실패 {resp.status_code}: {resp.text[:300]}")
    body = resp.json()
    cache[scope_str] = {
        "access_token": body["access_token"],
        "expires_at": time.time() + int(body.get("expires_in", 3600)),
    }
    CACHE_PATH.write_text(json.dumps(cache))
    CACHE_PATH.chmod(0o600)
    return cache[scope_str]["access_token"]
