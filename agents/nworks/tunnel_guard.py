#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""터널 가드 — cloudflared quick tunnel 상시 감시·복구·콜백 재등록.

배경: quick tunnel URL은 재시작마다 바뀌고, 죽어도 조용히 실패 루프에 빠진다
(2026-08-13 06:00~15:00 9시간 무중단 장애 발생). 콜백이 끊기면 부서방 아카이빙과
봇 Q&A가 통째로 멈춘다. 이 가드가 10분마다:
  1) 현재 터널 URL을 로그에서 읽고 외부 도달을 확인
  2) 죽었으면 터널 재시작 → 새 URL 취득
  3) URL이 콘솔 등록값과 다르면 콜백을 **API로** 자동 재등록 (PATCH /bots/{botId})
  4) 재등록 성공/실패를 Roy에게 1:1로 알림 (실패 시 새 URL을 명시해 30초 수동 대응)

launchd: com.urban.nworks-tunnel-guard (600초). 2026-08-25 부터 재등록이 API 경로라
사람 손이 필요 없다 — 그전에는 브라우저를 띄워야 했고 launchd 에는 GUI 세션이 없어
매번 실패했다. 알림 폴백은 그대로 둔다.
"""
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

PROJ = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJ / "scripts"))
from nworks.client import NworksClient  # noqa: E402
from nworks.config import load_config  # noqa: E402
from nworks.domains import bot_send_text  # noqa: E402

CF_LOG = PROJ / "workspace" / "cloudflared_launchd.log"
GUARD_LOG = PROJ / "workspace" / "tunnel_guard.log"
STATE = Path.home() / ".nworks" / "tunnel_state.json"
URL_RE = re.compile(r"https://[a-z0-9-]+\.trycloudflare\.com")


def log(m: str) -> None:
    GUARD_LOG.parent.mkdir(exist_ok=True)
    with GUARD_LOG.open("a") as f:
        f.write(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {m}\n")


def current_url() -> str | None:
    """로그에서 현재 터널 URL을 읽는다.

    ⚠️ `api.trycloudflare.com` 을 반드시 걸러야 한다. cloudflared 는 quick tunnel 을
    요청하면서 이 API 호스트를 로그에 찍는데, 정규식에 걸리는 데다 **실제로 200 을
    응답한다.** 그대로 두면 가드는 터널이 살아 있다고 믿고 아무것도 하지 않는다.
    2026-08-14~18 나흘간 부서방 아카이빙이 조용히 멈춘 원인이 이것이다.
    """
    try:
        urls = [u for u in URL_RE.findall(CF_LOG.read_text())
                if not u.startswith("https://api.")]
        return urls[-1] if urls else None
    except OSError:
        return None


def reachable(url: str) -> bool:
    try:
        r = subprocess.run(["curl", "-s", "-o", "/dev/null", "-w", "%{http_code}",
                            "--max-time", "10", url + "/"], capture_output=True, text=True)
        return r.stdout.strip() not in ("000", "")
    except Exception:
        return False


def restart_tunnel() -> None:
    subprocess.run(["launchctl", "kickstart", "-k", "gui/502/com.urban.nworks-tunnel"],
                   capture_output=True)
    time.sleep(15)


def load_state() -> dict:
    try:
        return json.loads(STATE.read_text())
    except Exception:
        return {}


def save_state(d: dict) -> None:
    STATE.write_text(json.dumps(d, ensure_ascii=False))
    os.chmod(STATE, 0o600)


def try_reregister(new_url: str) -> bool:
    """콜백 URL을 **API로** 교체한다. `PATCH /bots/{botId}`.

    브라우저 자동화를 버린 이유 — launchd 에서 도는 프로세스에는 GUI 세션이 없어
    `cmux browser open` 이 표면을 얻지 못한다(2026-08-25 실측: 「브라우저 표면 확보 실패」).
    macOS 가 막는 방향이라 앞으로도 간헐적으로만 성공한다.

    API 는 세션도 화면도 필요 없다. 앱에 `bot` 스코프를 추가해(2026-08-25) 이 경로가 열렸다.
    """
    cb = new_url + "/callback"
    try:
        sys.path.insert(0, str(PROJ / "scripts"))
        import requests
        from nworks.auth import get_token
        cfg = load_config()
        bot = cfg["bot_id"]
        h = {"Authorization": f"Bearer {get_token(['bot'])}",
             "Content-Type": "application/json"}
        base = f"https://www.worksapis.com/v1.0/bots/{bot}"

        cur = requests.get(base, headers=h, timeout=30)
        if cur.status_code != 200:
            log(f"재등록 중단: 봇 조회 실패 {cur.status_code} {cur.text[:120]}")
            return False
        cur = cur.json()
        if cur.get("callbackUrl") == cb:
            return True                      # 이미 맞다

        # 이름·이벤트를 그대로 실어 보낸다. 빼면 서버가 비운다.
        body = {"botName": cur.get("botName"), "enableCallback": True,
                "callbackUrl": cb,
                "callbackEvents": cur.get("callbackEvents") or ["text"]}
        r = requests.patch(base, headers=h, json=body, timeout=30)
        if r.status_code != 200:
            log(f"재등록 중단: PATCH 실패 {r.status_code} {r.text[:120]}")
            return False

        # 서버가 실제로 받았는지 다시 읽어 확인한다. 200 만 믿지 않는다.
        back = requests.get(base, headers=h, timeout=30).json()
        if back.get("callbackUrl") == cb:
            return True
        log(f"재등록 중단: 저장 후 값이 다름 — {back.get('callbackUrl')}")
        return False
    except Exception as e:
        log(f"재등록 시도 실패: {str(e)[:150]}")
        return False


def alert(msg: str) -> None:
    try:
        cfg = load_config()
        bot_send_text(NworksClient(), cfg["bot_id"], msg, user_id="{{OPERATOR_ACCOUNT}}")
    except Exception as e:
        log(f"알림 실패: {str(e)[:120]}")


def main() -> None:
    url = current_url()
    if not url:
        log("URL 미확인 — 로그 대기")
        return
    alive = reachable(url)
    if not alive:
        log(f"터널 불통 {url} — 재시작")
        restart_tunnel()
        url = current_url()
        if not url or not reachable(url):
            log("재시작 후에도 불통")
            alert("⚠️ 네이버웍스 터널 불통 — 재시작 실패. tunnel_guard.log 확인")
            return
        log(f"재시작 성공 → {url}")

    state = load_state()
    registered = state.get("registered_url")
    if url == registered:
        return  # 안정 — 아무것도 안 함
    log(f"URL 변경 감지: {registered} → {url}")
    if try_reregister(url):
        save_state({"registered_url": url, "at": time.strftime("%Y-%m-%d %H:%M:%S")})
        log(f"콘솔 콜백 재등록 완료 → {url}")
        alert(f"✅ 터널 URL 변경 → 콜백 자동 재등록 완료\n{url}/callback")
    else:
        # 같은 URL로 계속 실패하는 동안 10분마다 알리면 알림 피로로 오히려 무시된다.
        # 3시간에 한 번만 알린다 — 조용한 장기 장애만 막으면 되고, 그 사이에도
        # 가드는 매 사이클 재등록을 다시 시도한다.
        last = state.get("alerted_at", 0) if state.get("pending_url") == url else 0
        save_state({"registered_url": None, "pending_url": url,
                    "alerted_at": last or time.time()})
        log(f"자동 재등록 실패 — 수동 필요 {url}")
        if not last or time.time() - last > 3 * 3600:
            save_state({"registered_url": None, "pending_url": url,
                        "alerted_at": time.time()})
            alert(f"⚠️ 터널 URL이 바뀌었습니다. 콘솔 콜백을 아래로 수동 등록해주세요:\n"
                  f"{url}/callback\n(Developer Console → Bot 12875024 → Callback URL)")


if __name__ == "__main__":
    main()
