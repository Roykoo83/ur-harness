#!/usr/bin/env python3
"""Confluence 접근 계층 — API 토큰으로 읽는다.

  환경변수 CONFLUENCE_EMAIL + CONFLUENCE_TOKEN, 또는
  ~/.config/{{WORKSPACE_SLUG}}/confluence.json
  토큰 발급: https://id.atlassian.com/manage-profile/security/api-tokens

자동 실행에서 돌기 때문에 **토큰이 없으면 명확히 실패시킨다.** 조용히 다른 길로
새지 않는다 — 폴백이 조용히 켜지면 「토큰 설정 완료」가 거짓이 된다.

브라우저 세션을 빌려 쓰는 방식도 가능하다. 그것은 개인 환경에 묶이므로
이 저장소의 실행 경로에는 넣지 않았다 — `참고/브라우저_에이전트_패턴/` 에 적어 두었다.
"""
from __future__ import annotations

import base64
import json
import os
import subprocess
import urllib.error
import urllib.request
from pathlib import Path

BASE = "{{WIKI_BASE}}"
CONF_DIR = Path.home() / ".config" / "{{WORKSPACE_SLUG}}"
CRED_FILE = CONF_DIR / "confluence.json"
CHUNK = 60000  # 브라우저 eval 응답 크기 제한 때문에 나눠 받는다


class ConfluenceError(RuntimeError):
    pass


# --------------------------------------------------------------------------- 토큰

class TokenClient:
    """Atlassian API 토큰 기반. 스케줄 실행에서 쓰는 정식 경로."""

    mode = "token"

    def __init__(self, email: str, token: str):
        raw = f"{email}:{token}".encode()
        self._auth = "Basic " + base64.b64encode(raw).decode()

    def _req(self, path: str, raw: bool = False):
        url = path if path.startswith("http") else BASE + path
        req = urllib.request.Request(url, headers={
            "Authorization": self._auth,
            "Accept": "*/*" if raw else "application/json",
        })
        try:
            with urllib.request.urlopen(req, timeout=90) as r:
                body = r.read()
        except urllib.error.HTTPError as e:
            raise ConfluenceError(f"{e.code} {url}") from e
        return body if raw else json.loads(body)

    def get_json(self, path: str):
        return self._req(path)

    def get_bytes(self, path: str) -> bytes:
        return self._req(path, raw=True)


# --------------------------------------------------------------------------- 브라우저


def get_client(allow_browser: bool = False):
    """API 클라이언트를 돌려준다. 자격증명이 없으면 그 자리에서 멈춘다.

    `allow_browser` 는 호환을 위해 남겨 둔 인자다 — 이 저장소에서는 쓰지 않는다.
    """
    email = os.environ.get("CONFLUENCE_EMAIL")
    token = os.environ.get("CONFLUENCE_TOKEN")
    if not (email and token) and CRED_FILE.is_file():
        try:
            d = json.loads(CRED_FILE.read_text(encoding="utf-8"))
            email, token = d.get("email"), d.get("token")
        except (OSError, ValueError) as e:
            raise ConfluenceError(f"{CRED_FILE} 를 읽지 못했다: {e}") from e
    if not (email and token):
        raise ConfluenceError(
            "Confluence 자격증명이 없다.\n"
            f"  환경변수 CONFLUENCE_EMAIL·CONFLUENCE_TOKEN 또는 {CRED_FILE} 를 만든다.\n"
            "  토큰: https://id.atlassian.com/manage-profile/security/api-tokens")
    return TokenClient(email, token)
