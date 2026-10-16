"""User Account OAuth for NAVER WORKS (Mail/Drive are UA-only — SA tokens rejected).

Flow (api_reference.md §1-②③):
  authorize (browser, member login) -> code (10min, single-use)
  -> token exchange -> access_token (1h) + refresh_token (90d, rotates when RT Rotation On)

Tokens cached per account at ~/.nworks/ua_token_<local-part>.json (0600).
Never print tokens.
"""
from __future__ import annotations

import http.server
import json
import os
import secrets
import threading
import time
import urllib.parse

import requests

from .config import load_config
from .endpoints import AUTHORIZE_URL, TOKEN_URL

CALLBACK_PORT = 8976
REDIRECT_URI = f"http://localhost:{CALLBACK_PORT}/callback"
SKEW = 120


def _cache_path(account: str) -> str:
    local = account.split("@")[0]
    return os.path.expanduser(f"~/.nworks/ua_token_{local}.json")


def _save(path: str, data: dict) -> None:
    data["saved_at"] = int(time.time())
    with open(path, "w") as f:
        json.dump(data, f)
    os.chmod(path, 0o600)


def build_authorize_url(scope: str = "mail.read") -> tuple[str, str]:
    cfg = load_config()
    state = secrets.token_urlsafe(16)
    q = urllib.parse.urlencode({
        "client_id": cfg["client_id"],
        "redirect_uri": REDIRECT_URI,
        "scope": scope,
        "response_type": "code",
        "state": state,
    })
    return f"{AUTHORIZE_URL}?{q}", state


def wait_for_code(expected_state: str, timeout: int = 600) -> str:
    """Run a one-shot localhost callback server and return the auth code."""
    result: dict = {}
    done = threading.Event()

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802
            qs = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            if self.path.startswith("/callback"):
                if qs.get("state", [""])[0] != expected_state:
                    result["error"] = "state mismatch (CSRF?)"
                elif "code" in qs:
                    result["code"] = qs["code"][0]
                else:
                    result["error"] = f"no code: {qs.get('error', ['?'])[0]}"
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.end_headers()
                msg = "인증 완료 — 이 탭은 닫아도 됩니다." if "code" in result else "인증 실패"
                self.wfile.write(f"<h3>{msg}</h3>".encode())
                done.set()

        def log_message(self, *a):  # silence
            pass

    srv = http.server.HTTPServer(("127.0.0.1", CALLBACK_PORT), Handler)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    try:
        if not done.wait(timeout):
            raise TimeoutError("인증 대기 시간 초과 (10분)")
    finally:
        srv.shutdown()
    if "error" in result:
        raise RuntimeError(result["error"])
    return result["code"]


def exchange_code(code: str, account: str) -> None:
    cfg = load_config()
    resp = requests.post(TOKEN_URL, data={
        "code": code,
        "grant_type": "authorization_code",
        "client_id": cfg["client_id"],
        "client_secret": cfg["client_secret"],
    }, timeout=30)
    if resp.status_code != 200:
        raise RuntimeError(f"UA 토큰 교환 실패 {resp.status_code}: {resp.text[:300]}")
    _save(_cache_path(account), resp.json())


def get_ua_token(account: str) -> str:
    """Return a valid UA access token, refreshing via RT when expired."""
    path = _cache_path(account)
    if not os.path.exists(path):
        raise RuntimeError(
            f"{account} UA 토큰 없음 — 'nwctl ua-auth {account}' 로 1회 로그인 동의 필요")
    with open(path) as f:
        tok = json.load(f)
    age = time.time() - tok.get("saved_at", 0)
    if age < int(tok.get("expires_in", 3600)) - SKEW:
        return tok["access_token"]
    cfg = load_config()
    resp = requests.post(TOKEN_URL, data={
        "refresh_token": tok["refresh_token"],
        "grant_type": "refresh_token",
        "client_id": cfg["client_id"],
        "client_secret": cfg["client_secret"],
    }, timeout=30)
    if resp.status_code != 200:
        raise RuntimeError(
            f"UA 토큰 갱신 실패 {resp.status_code} — RT 만료(90일) 시 재동의 필요: {resp.text[:200]}")
    new = resp.json()
    if "refresh_token" not in new:  # rotation off keeps old RT
        new["refresh_token"] = tok["refresh_token"]
    _save(path, new)
    return new["access_token"]
