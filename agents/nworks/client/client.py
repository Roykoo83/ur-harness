"""HTTP client for NAVER WORKS API.

Built-in behaviors (per api_survey.md):
- refuses to call endpoints not yet verified against research/api_reference.md
- 429 backoff honoring Retry-After, exponential fallback (max 3 retries)
- surfaces rateLimit-remaining header for callers/logs
- cursor pagination generator (responseMetaData.nextCursor)
"""
from __future__ import annotations

import time

import requests

from .auth import get_token
from .endpoints import API_HOST, ENDPOINTS, NO_CURSOR_KEYS, REDIRECT_KEYS


class UnverifiedEndpointError(RuntimeError):
    pass


class NworksClient:
    def __init__(self, allow_unverified: bool = False, ua_account: str | None = None):
        """ua_account: member email for UA-only APIs (Mail/Drive) — uses UA token
        from ua_auth instead of the SA token."""
        self.allow_unverified = allow_unverified
        self.ua_account = ua_account
        self.last_rate_remaining: str | None = None

    def call(self, key: str, path_params: dict | None = None,
             params: dict | None = None, json_body: dict | None = None) -> dict:
        method, path, scope, verified = ENDPOINTS[key]
        if not verified and not self.allow_unverified:
            raise UnverifiedEndpointError(
                f"'{key}' 는 api_reference.md 검증 전 — endpoints.py의 verified=True 전에는 호출 금지")
        url = API_HOST + path.format(**(path_params or {}))
        if self.ua_account:
            from .ua_auth import get_ua_token
            token = get_ua_token(self.ua_account)
        else:
            token = get_token([scope])
        follow = key not in REDIRECT_KEYS
        for attempt in range(4):
            resp = requests.request(
                method, url,
                headers={"Authorization": f"Bearer {token}"},
                params=params, json=json_body, timeout=30,
                allow_redirects=follow)
            self.last_rate_remaining = resp.headers.get("rateLimit-remaining")
            if resp.status_code == 429:
                wait = int(resp.headers.get("Retry-After", 2 ** attempt * 5))
                time.sleep(wait)
                continue
            if resp.status_code in (301, 302) and not follow:
                return {"location": resp.headers.get("location")}
            if resp.status_code >= 400:
                raise RuntimeError(f"{key} 실패 {resp.status_code}: {resp.text[:300]}")
            return resp.json() if resp.content else {}
        raise RuntimeError(f"{key}: 429 재시도 3회 초과 — 호출 주기를 늘리세요")

    def paginate(self, key: str, item_field: str, path_params: dict | None = None,
                 params: dict | None = None):
        """Yield items across cursor pages (responseMetaData.nextCursor)."""
        if key in NO_CURSOR_KEYS:
            raise ValueError(f"{key}: 커서 미지원 API — call()로 기간 분할 조회하세요")
        params = dict(params or {})
        while True:
            body = self.call(key, path_params=path_params, params=params)
            yield from body.get(item_field, [])
            cursor = (body.get("responseMetaData") or {}).get("nextCursor")
            if not cursor:
                return
            params["cursor"] = cursor
