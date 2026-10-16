"""Thin domain wrappers — one function per business need."""
from __future__ import annotations

from .client import NworksClient


def bot_send_text(client: NworksClient, bot_id: str, text: str,
                  user_id: str | None = None, channel_id: str | None = None) -> dict:
    body = {"content": {"type": "text", "text": text}}
    if user_id:
        return client.call("bot.send_user",
                           {"bot_id": bot_id, "user_id": user_id}, json_body=body)
    if channel_id:
        return client.call("bot.send_channel",
                           {"bot_id": bot_id, "channel_id": channel_id}, json_body=body)
    raise ValueError("user_id 또는 channel_id 필요")


def bot_send_file(client: NworksClient, bot_id: str, file_path: str,
                  user_id: str | None = None, channel_id: str | None = None,
                  file_name: str | None = None) -> dict:
    """Send a file via bot. Three steps, verified 2026-09-21.

    1. POST /bots/{bot_id}/attachments {"fileName": ...} -> {uploadUrl, fileId}
    2. POST uploadUrl  multipart field "Filedata" -> {fileId, fileSize, fileName}
    3. POST .../messages  {"content": {"type": "file", "fileId": ...}} -> 201

    uploadUrl points at apis-storage.worksmobile.com (outside API_HOST), so step 2
    bypasses the endpoint table and calls requests directly with the same token.
    """
    import mimetypes
    from pathlib import Path

    import requests

    from .auth import get_token

    path = Path(file_path)
    if not path.is_file():
        raise FileNotFoundError(file_path)
    name = file_name or path.name

    issued = client.call("bot.attachment", {"bot_id": bot_id},
                         json_body={"fileName": name})
    upload_url, file_id = issued["uploadUrl"], issued["fileId"]

    token = get_token(["bot.message"])
    ctype = mimetypes.guess_type(name)[0] or "application/octet-stream"
    with path.open("rb") as fh:
        resp = requests.post(upload_url,
                             headers={"Authorization": f"Bearer {token}"},
                             files={"Filedata": (name, fh, ctype)}, timeout=180)
    if resp.status_code >= 400:
        raise RuntimeError(f"bot file upload 실패 {resp.status_code}: {resp.text[:300]}")

    body = {"content": {"type": "file", "fileId": file_id}}
    if user_id:
        client.call("bot.send_user", {"bot_id": bot_id, "user_id": user_id}, json_body=body)
    elif channel_id:
        client.call("bot.send_channel", {"bot_id": bot_id, "channel_id": channel_id}, json_body=body)
    else:
        raise ValueError("user_id 또는 channel_id 필요")
    return resp.json() if resp.content else {"fileId": file_id}


def directory_users(client: NworksClient):
    yield from client.paginate("directory.users", item_field="users")


def calendar_events(client: NworksClient, user_id: str, calendar_id: str,
                    from_dt: str, until_dt: str) -> list:
    """기간 조회 (커서 없음 · 최대 31일 · '+'는 %2B 인코딩된 ISO 문자열로)."""
    body = client.call("calendar.events",
                       path_params={"user_id": user_id, "calendar_id": calendar_id},
                       params={"fromDateTime": from_dt, "untilDateTime": until_dt})
    return body.get("events", [])


def audit_download_url(client: NworksClient, **params) -> str:
    """감사 로그 CSV 다운로드 URL 반환 (302 location · 기간 최대 31일 · 10 req/min)."""
    return client.call("audit.download", params=params)["location"]
