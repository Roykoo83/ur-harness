#!/usr/bin/env python3
"""Fathom 접근 계층 — API 키 우선, MCP(OAuth) 폴백.

무료 플랜에서 **녹화·전사·저장은 무제한**이고 AI 요약만 월 5회로 제한된다.
따라서 이 파이프라인은 Fathom의 AI 요약을 쓰지 않고 **원문 전사만 가져와서**
회의록은 우리가 직접 생성한다. 그러면 무료 플랜으로도 주 2~3회 회의를 감당한다.

인증 (둘 중 하나)
  1) API 키 — Fathom User Settings → API Access 에서 발급
     ~/.config/{{WORKSPACE_SLUG}}/fathom.json  {"api_key": "..."}  또는 FATHOM_API_KEY
     rate limit: 전체 키 합산 60 calls/min
  2) MCP(OAuth) — `claude mcp add fathom` 로 인증해 둔 토큰을 재사용
     대화형 세션에서는 잘 되지만 스케줄 실행에는 1번을 권장한다.
"""
from __future__ import annotations

import json
import os
import queue
import subprocess
import threading
import urllib.error
import urllib.request
from pathlib import Path

API_BASE = "https://api.fathom.ai/external/v1"
MCP_URL = "https://api.fathom.ai/mcp"
CRED = Path.home() / ".config" / "{{WORKSPACE_SLUG}}" / "fathom.json"


class FathomError(RuntimeError):
    pass


# --------------------------------------------------------------------------- API 키

class ApiClient:
    mode = "api"

    def __init__(self, key: str):
        self.key = key

    def _get(self, path: str, params: dict | None = None):
        from urllib.parse import urlencode
        url = API_BASE + path + (("?" + urlencode(params, doseq=True)) if params else "")
        req = urllib.request.Request(url, headers={
            "X-Api-Key": self.key, "Accept": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=90) as r:
                return json.loads(r.read())
        except urllib.error.HTTPError as ex:
            raise FathomError(f"{ex.code} {url}") from ex

    def list_meetings(self, created_after: str | None = None) -> list[dict]:
        out, cursor = [], None
        while True:
            p: dict = {}
            if created_after:
                p["created_after"] = created_after
            if cursor:
                p["cursor"] = cursor
            j = self._get("/meetings", p)
            out.extend(j.get("items") or j.get("data") or [])
            cursor = j.get("next_cursor") or (j.get("meta") or {}).get("next_cursor")
            if not cursor:
                break
        return out

    def transcript(self, recording_id: str | int) -> str:
        j = self._get(f"/recordings/{recording_id}/transcript")
        if isinstance(j, str):
            return j
        segs = j.get("segments") or j.get("transcript") or []
        if isinstance(segs, str):
            return segs
        return "\n".join(
            f"[{s.get('timestamp') or s.get('start') or ''}] "
            f"{s.get('speaker', {}).get('name') if isinstance(s.get('speaker'), dict) else s.get('speaker', '')}: "
            f"{s.get('text', '')}" for s in segs)


# --------------------------------------------------------------------------- MCP

class McpClient:
    """`mcp-remote` 를 stdio 로 띄워 JSON-RPC 로 직접 호출한다."""

    mode = "mcp"

    def __init__(self):
        self._proc = None

    def _start(self):
        env = {k: v for k, v in os.environ.items() if k != "NODE_OPTIONS"}
        self._proc = subprocess.Popen(
            ["npx", "-y", "mcp-remote@latest", MCP_URL],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL, env=env, text=True, bufsize=1)
        self._q: queue.Queue = queue.Queue()

        def reader():
            for line in self._proc.stdout:
                line = line.strip()
                if line.startswith("{"):
                    try:
                        self._q.put(json.loads(line))
                    except Exception:
                        pass
        threading.Thread(target=reader, daemon=True).start()
        self._id = 0
        self._rpc("initialize", {"protocolVersion": "2024-11-05", "capabilities": {},
                                 "clientInfo": {"name": "{{WORKSPACE_SLUG}}", "version": "1"}})
        self._notify("notifications/initialized")

    def _send(self, msg: dict):
        self._proc.stdin.write(json.dumps(msg) + "\n")
        self._proc.stdin.flush()

    def _notify(self, method: str):
        self._send({"jsonrpc": "2.0", "method": method})

    def _rpc(self, method: str, params: dict | None = None, timeout: int = 180):
        import time
        self._id += 1
        mid = self._id
        m = {"jsonrpc": "2.0", "id": mid, "method": method}
        if params is not None:
            m["params"] = params
        self._send(m)
        end = time.time() + timeout
        while time.time() < end:
            try:
                msg = self._q.get(timeout=2)
            except queue.Empty:
                continue
            if msg.get("id") == mid:
                if "error" in msg:
                    raise FathomError(str(msg["error"])[:300])
                return msg.get("result", {})
        raise FathomError(f"{method} 응답 시간 초과")

    def _tool(self, name: str, args: dict | None = None) -> str:
        if not self._proc:
            self._start()
        r = self._rpc("tools/call", {"name": name, "arguments": args or {}})
        return "".join(c.get("text", "") for c in r.get("content", []))

    def close(self):
        if self._proc:
            self._proc.terminate()
            self._proc = None

    # -- MCP 도구는 사람이 읽는 텍스트를 준다. 필요한 필드만 뽑아 쓴다. --

    def list_meetings(self, created_after: str | None = None) -> list[dict]:
        import re
        txt = self._tool("list_meetings", {})
        out = []
        for ln in txt.splitlines():
            m = re.match(r"-\s*(?P<title>.+?)\s*\|\s*(?P<date>\d{4}-\d{2}-\d{2})\s*\|"
                         r"\s*id:\s*(?P<rid>\d+)\s*\|\s*url:\s*(?P<url>\S+)"
                         r"(?:\s*\|\s*recorded by\s*(?P<by>.+))?", ln.strip())
            if not m:
                continue
            d = m.groupdict()
            if created_after and d["date"] < created_after[:10]:
                continue
            out.append({"recording_id": d["rid"], "title": d["title"].strip(),
                        "date": d["date"], "url": d["url"],
                        "recorded_by": (d.get("by") or "").strip()})
        return out

    def transcript(self, recording_id: str | int) -> str:
        return self._tool("get_meeting_transcript",
                          {"recording_id": int(recording_id)})


# --------------------------------------------------------------------------- 선택

def get_client(prefer_api: bool = True):
    key = os.environ.get("FATHOM_API_KEY")
    if not key and CRED.exists():
        key = json.loads(CRED.read_text(encoding="utf-8")).get("api_key")
    if key and prefer_api:
        return ApiClient(key)
    return McpClient()
