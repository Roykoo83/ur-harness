#!/usr/bin/env python3
"""Confluence 접근 계층 — 인증 방식을 두 가지 중 자동 선택한다.

1. API 토큰 (권장, 스케줄 실행용)
   환경변수 CONFLUENCE_EMAIL + CONFLUENCE_TOKEN, 또는 ~/.config/{{WORKSPACE_SLUG}}/confluence.json
   토큰 발급: https://id.atlassian.com/manage-profile/security/api-tokens

2. cmux 브라우저 세션 (대화형 폴백)
   이미 로그인된 브라우저 안에서 fetch를 실행한다. 세션이 끊기면 실패한다.

launchd에서 돌 때는 브라우저가 없을 수 있으므로 1번이 없으면 명확히 실패시킨다.
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

class BrowserClient:
    """cmux 브라우저의 로그인 세션을 빌려 쓴다. 대화형 세션 전용 폴백."""

    mode = "browser"

    def __init__(self, surface: str):
        self.surface = surface

    def _eval(self, js: str, timeout: int = 180) -> str:
        r = subprocess.run(
            ["cmux", "browser", "--surface", self.surface, "eval", js],
            capture_output=True, timeout=timeout,
        )
        out = r.stdout.decode("utf-8", "replace").strip()
        err = r.stderr.decode("utf-8", "replace").strip()
        # cmux는 오류를 stderr로도 stdout으로도 낸다. 둘 다 본다.
        if r.returncode != 0 or out.startswith("Error:") or (not out and err):
            raise ConfluenceError((out or err or f"exit {r.returncode}")[:300])
        return out

    def get_json(self, path: str):
        js = (f"(async()=>{{const r=await fetch({json.dumps(path)},"
              "{headers:{Accept:'application/json'}});return await r.text();})()")
        return json.loads(self._eval(js))

    def get_bytes(self, path: str) -> bytes:
        """base64 조각으로 나눠 받아 다시 붙인다."""
        out = b""
        for i in range(0, 400):  # 400 * 60KB = 24MB 상한
            js = (
                f"(async()=>{{const r=await fetch({json.dumps(path)},{{redirect:'follow'}});"
                "const b=new Uint8Array(await r.arrayBuffer());"
                f"const st={i}*{CHUNK},en=Math.min(st+{CHUNK},b.length);"
                "if(st>=b.length)return 'EOF';"
                "let s='';for(let k=st;k<en;k++)s+=String.fromCharCode(b[k]);return btoa(s);})()"
            )
            part = self._eval(js)
            if part == "EOF":
                break
            out += base64.b64decode(part)
        return out


# --------------------------------------------------------------------------- 선택

def _find_surface() -> str | None:
    """이미 Confluence가 열려 있는 브라우저 서피스를 찾는다."""
    for sid in (os.environ.get("CONFLUENCE_SURFACE"),):
        if sid:
            return sid
    # 서피스 목록 조회 API가 없으므로 새로 연다
    try:
        r = subprocess.run(["cmux", "browser", "open", BASE + "/wiki/"],
                           capture_output=True, timeout=60)
        out = r.stdout.decode("utf-8", "replace")
        for tok in out.split():
            if tok.startswith("surface="):
                return tok.split("=", 1)[1]
    except Exception:
        return None
    return None


def get_client(allow_browser: bool = True):
    email = os.environ.get("CONFLUENCE_EMAIL")
    token = os.environ.get("CONFLUENCE_TOKEN")
    if not (email and token) and CRED_FILE.exists():
        d = json.loads(CRED_FILE.read_text(encoding="utf-8"))
        email, token = d.get("email"), d.get("token")
    if email and token:
        cli = TokenClient(email, token)
        # 토큰이 있다고 **살아 있는 것은 아니다.** 2026-08-25 에 만료된 토큰을 그대로 들고
        # 403 으로 죽었다. 한 번 찔러 보고 안 되면 브라우저 세션으로 넘어간다 —
        # 값이 있다는 이유로 죽은 자격증명을 고집하면 폴백이 있어도 쓰이지 않는다.
        try:
            cli.get_json("/wiki/rest/api/space?limit=1")
            return cli
        except ConfluenceError as e:
            if not allow_browser:
                raise ConfluenceError(f"API 토큰이 거부됐습니다({e}). 토큰 재발급 필요.") from e
            print(f"[경고] API 토큰 거부({e}) — 브라우저 세션으로 전환", flush=True)

    if not allow_browser:
        raise ConfluenceError(
            "API 토큰이 없습니다. ~/.config/{{WORKSPACE_SLUG}}/confluence.json 에 "
            '{"email": "...", "token": "..."} 를 두거나 '
            "CONFLUENCE_EMAIL/CONFLUENCE_TOKEN 환경변수를 설정하십시오.")

    surface = _find_surface()
    if not surface:
        raise ConfluenceError("API 토큰도 없고 브라우저 세션도 열 수 없습니다.")
    return BrowserClient(surface)
