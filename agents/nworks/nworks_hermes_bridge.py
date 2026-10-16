"""NAVER WORKS ↔ Hermes bridge — 부서방 질문을 Hermes로 넘기고 답을 봇으로 회신.

흐름: NAVER WORKS Callback(POST) → 서명 검증(Bot Secret HMAC-SHA256, X-WORKS-Signature)
     → 발신자 정책 → `hermes -z` 원샷 → bot send API 회신

정책 (Roy 확정 2026-08-04):
- 화이트리스트: calendar_targets.json의 5인만 (그 외 무응답)
- Roy({{OPERATOR_USER}}@)만 전체 권한. 나머지는 읽기 전용 — 프롬프트에 조회·요약 제약 명시
- Hermes 승인 3종(파괴적 변경·외부 전송·비용 발생) 규약은 Hermes 쪽 그대로 유지

Run: python3 nworks_hermes_bridge.py  (port 8977; cloudflared tunnel이 전면)
"""
import base64
import hashlib
import hmac
import json
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

PROJ = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJ / "scripts"))
from nworks.client import NworksClient  # noqa: E402
from nworks.config import load_config  # noqa: E402
from nworks.domains import bot_send_text  # noqa: E402

PORT = 8977
LOG = PROJ / "workspace" / "hermes_bridge.log"
HERMES_TIMEOUT = 300

FULL_ACCESS = {"{{OPERATOR_ACCOUNT}}"}

READONLY_PREFIX = (
    "[읽기 전용 요청 - 반드시 준수] 아래 질문에 조회·요약으로만 답하라. "
    "파일 생성·수정·삭제, 셸 명령 실행, 외부 전송, 비용 발생 작업은 금지다.\n"
    "[데이터 원천: 아래 경로를 직접 읽어라. 없으면 없다고 답하고 추측하지 마라]\n"
    "1. 태스크 원장(무엇이 진행·막힘·완료인지의 정본): "
    "'{{HOME}}/{{WORKSPACE_NAME}}/projects/rpbd-infra/data/tasks/ledger.jsonl' "
    "각 줄이 JSON. state=planned|doing|blocked|done, title·owner·due·issue 필드. "
    "'막힌 것'을 물으면 state=blocked를, '완료'를 물으면 state=done을 근거로 답하라.\n"
    "2. 일정: '{{HOME}}/{{WORKSPACE_NAME}}/projects/nworks-agent/output/stage/calendar/YYYY-MM.md'\n"
    "3. 메일: '.../output/stage/mail_{{BOT_USER}}/YYYY-MM/' (현행 rbp-) 와 "
    "'.../output/stage/mail_{{OPERATOR_USER}}/YYYY-MM/' (2026-06~08/12 과거분 roy-). 둘이 한 코퍼스다. "
    "업무 무관(시스템 알림·마케팅·중복) 여부는 '.../output/stage/mail_대장.tsv' 의 promote 열로 판정하라\n"
    "4. 부서 대화: '.../output/stage/messenger/<방>/YYYY-MM.md' (dept·과제예산·AX_TF)\n"
    "5. 확정 문서(과제·예산·인허가): "
    "'{{MOUNT_ROOT}}/공유 드라이브/{{SSOT_DRIVE}}/' "
    "폴더 내용은 각 폴더의 _INDEX.md를 먼저 읽어 판단하라.\n"
    "주의: 일정표에 항목이 있는 것은 '예정'일 뿐 완료가 아니다. 완료 여부는 반드시 원장으로 판정하라.\n"
    "[답변 규칙] 간결한 한국어. 근거가 된 값을 함께 밝힌다. "
    "개인 실명과 금액을 한 줄에 함께 쓰지 않는다. 모르면 '확인 필요'라고 쓴다. "
    "타 부서·개인에 등급·순위·평가를 붙이지 않는다.\n"
    "[열람 범위] 이 답변은 대표와 부서원이 함께 있는 방에 그대로 노출될 수 있다.\n\n질문: ")

ROY_PREFIX = (
    "[네이버웍스 창구 - Roy 본인 요청]\n"
    "[데이터 원천: 아래 경로를 직접 읽어라. 없으면 없다고 답하고 추측하지 마라]\n"
    "1. 태스크 원장(무엇이 진행·막힘·완료인지의 정본): "
    "'{{HOME}}/{{WORKSPACE_NAME}}/projects/rpbd-infra/data/tasks/ledger.jsonl' "
    "각 줄이 JSON. state=planned|doing|blocked|done, title·owner·due·issue 필드. "
    "'막힌 것'을 물으면 state=blocked를, '완료'를 물으면 state=done을 근거로 답하라.\n"
    "2. 일정: '{{HOME}}/{{WORKSPACE_NAME}}/projects/nworks-agent/output/stage/calendar/YYYY-MM.md'\n"
    "3. 메일: '.../output/stage/mail_{{BOT_USER}}/YYYY-MM/' (현행 rbp-) 와 "
    "'.../output/stage/mail_{{OPERATOR_USER}}/YYYY-MM/' (2026-06~08/12 과거분 roy-). 둘이 한 코퍼스다. "
    "업무 무관(시스템 알림·마케팅·중복) 여부는 '.../output/stage/mail_대장.tsv' 의 promote 열로 판정하라\n"
    "4. 부서 대화: '.../output/stage/messenger/<방>/YYYY-MM.md' (dept·과제예산·AX_TF)\n"
    "5. 확정 문서(과제·예산·인허가): "
    "'{{MOUNT_ROOT}}/공유 드라이브/{{SSOT_DRIVE}}/' "
    "폴더 내용은 각 폴더의 _INDEX.md를 먼저 읽어 판단하라.\n"
    "주의: 일정표에 항목이 있는 것은 '예정'일 뿐 완료가 아니다. 완료 여부는 반드시 원장으로 판정하라.\n"
    "답은 간결한 한국어로. 근거가 된 값을 함께 밝힌다.\n\n요청: ")


def _log(msg: str) -> None:
    import datetime
    LOG.parent.mkdir(exist_ok=True)
    with LOG.open("a") as f:
        f.write(f"[{datetime.datetime.now():%Y-%m-%d %H:%M:%S}] {msg}\n")


def _load_whitelist() -> dict:
    cfg = json.loads((PROJ / "scripts" / "calendar_targets.json").read_text())
    return {t["email"].lower(): t["name"] for t in cfg["targets"]}


def _resolve_emails(client: NworksClient, user_ids: set) -> dict:
    out = {}
    for uid in user_ids:
        try:
            u = client.call("directory.user", path_params={"user_id": uid})
            out[uid] = (u.get("email") or "").lower()
        except Exception as e:
            _log(f"directory.user 실패 {uid}: {str(e)[:100]}")
    return out


class Bridge(BaseHTTPRequestHandler):
    cfg = load_config()
    client = NworksClient()
    whitelist = _load_whitelist()
    email_cache: dict = {}
    busy = threading.Semaphore(2)  # hermes 동시 실행 제한

    def _verify(self, body: bytes) -> bool:
        sig = self.headers.get("X-WORKS-Signature", "")
        if not sig:
            _log("서명 헤더 없음 — 거부")
            return False
        # 실측 진단: bot_secret 외 후보 키도 대조해 어떤 키가 유효한지 로그로 확인
        for key_name in ("bot_secret", "client_secret", "client_id"):
            key = self.cfg.get(key_name, "")
            if not key:
                continue
            digest = hmac.new(key.encode(), body, hashlib.sha256).digest()
            if hmac.compare_digest(base64.b64encode(digest).decode(), sig):
                if key_name != "bot_secret":
                    _log(f"서명 일치 키 = {key_name} (bot_secret 아님 — 문서와 다름)")
                return True
        _log(f"서명 검증 실패 — sig[:12]={sig[:12]} body_len={len(body)} "
             f"bot_secret_len={len(self.cfg.get('bot_secret',''))}")
        dump = PROJ / "workspace" / "failed_callback_last.json"
        dump.write_text(json.dumps({"sig": sig, "body": body.decode("utf-8", "replace"),
                                    "headers": dict(self.headers)}, ensure_ascii=False))
        dump.chmod(0o600)
        return False

    def do_GET(self):  # health check
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"nworks-hermes-bridge ok")

    def do_POST(self):
        body = self.rfile.read(int(self.headers.get("Content-Length", 0)))
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"ok")  # 즉시 200 — 처리는 스레드로 (웍스 재전송 방지)
        if not self._verify(body):
            _log("서명 검증 실패 — 무시")
            return
        try:
            ev = json.loads(body)
        except ValueError:
            return
        threading.Thread(target=self._handle, args=(ev,), daemon=True).start()

    def _room_slug(self, channel: str) -> str | None:
        """이 채널을 아카이빙할지 판정하고 저장 폴더 슬러그를 반환. 아니면 None.
        부서방은 'dept'(기존 유지), config.archive_channels[{id,name}]는 name 슬러그."""
        if channel and channel == self.cfg.get("dept_channel_id"):
            return "dept"
        for c in self.cfg.get("archive_channels", []):
            if c.get("id") == channel:
                return c.get("name") or channel[:12]
        return None

    def _archive(self, ev: dict, uid: str, text: str, slug: str = "dept") -> None:
        """방 텍스트 1건을 raw JSONL에 실시간 append (API 호출 0, LLM 0)."""
        try:
            if uid not in self.email_cache:
                self.email_cache.update(_resolve_emails(self.client, {uid}))
            email = self.email_cache.get(uid, uid)
            import datetime
            issued = ev.get("issuedTime") or ""
            try:  # issuedTime은 UTC(Z) — 월 버킷·표시용 KST 병기
                kst = (datetime.datetime.fromisoformat(issued.replace("Z", "+00:00"))
                       .astimezone(datetime.timezone(datetime.timedelta(hours=9))))
            except ValueError:
                kst = datetime.datetime.now().astimezone()
            month = f"{kst:%Y-%m}"
            f = PROJ / "output" / "raw_messenger" / slug / f"{month}.jsonl"
            f.parent.mkdir(parents=True, exist_ok=True)
            with f.open("a") as fp:
                fp.write(json.dumps({
                    "time": issued, "kst": f"{kst:%Y-%m-%d %H:%M}",
                    "sender": self.whitelist.get(email, email),
                    "email": email, "text": text}, ensure_ascii=False) + "\n")
        except Exception as e:
            _log(f"archive 실패: {str(e)[:120]}")

    def _handle(self, ev: dict) -> None:
        if ev.get("type") != "message":
            return
        content = ev.get("content") or {}
        if content.get("type") != "text":
            return
        text = (content.get("text") or "").strip()
        source = ev.get("source") or {}
        uid = source.get("userId")
        channel = source.get("channelId")
        if not text or not uid:
            return
        # 지정 방 대화 전량 기록 (부서방 + config.archive_channels, 멘션 게이트보다 먼저)
        if channel:
            slug = self._room_slug(channel)
            if slug:
                self._archive(ev, uid, text, slug)
            else:  # 미지정 방 — 채널ID 발견용 진단 로그 (새 방 등록 시 참조)
                _log(f"미아카이빙 채널 메시지 channelId={channel}")
        # 채널에서는 @멘션일 때만 응답 (잡담 전체에 반응 금지). 1:1은 항상 응답.
        if channel:
            low = text.lower()
            if "urban ax bot" not in low and not low.startswith(("!ax", "ax봇", "봇아")):
                return
            for m in ("@Urban AX Bot", "urban ax bot", "!ax"):
                if text.lower().startswith(m.lower()):
                    text = text[len(m):].strip()
                    break
            if not text:
                text = "안녕하세요, 무엇을 도와드릴까요? (예: 오늘 일정 / 이번주 research@ 메일 요약)"

        if uid not in self.email_cache:
            self.email_cache.update(_resolve_emails(self.client, {uid}))
        email = self.email_cache.get(uid, "")
        name = self.whitelist.get(email)
        if not name:
            _log(f"비허용 발신 {email or uid} — 무응답")
            return

        prefix = ROY_PREFIX if email in FULL_ACCESS else READONLY_PREFIX
        _log(f"질문({name}{'·전체' if email in FULL_ACCESS else '·읽기전용'}): {text[:80]}")
        try:  # 즉시 수신 확인 — 생성에 1~3분 걸릴 수 있음을 안내
            ack = "질문 확인했습니다. 답변 생성 중입니다… (1~3분)"
            if channel:
                bot_send_text(self.client, self.cfg["bot_id"], ack, channel_id=channel)
            else:
                bot_send_text(self.client, self.cfg["bot_id"], ack, user_id=uid)
        except Exception as e:
            _log(f"ack 실패: {str(e)[:100]}")
        with self.busy:
            try:
                r = subprocess.run(
                    ["hermes", "-z", prefix + text] +
                    (["--safe-mode"] if email not in FULL_ACCESS else []),
                    capture_output=True, text=True, timeout=HERMES_TIMEOUT)
                answer = (r.stdout or "").strip() or "(응답 생성 실패 — 로그 확인 필요)"
            except subprocess.TimeoutExpired:
                answer = "(시간 초과 — 질문을 더 구체적으로 하거나 잠시 후 다시 시도해주세요)"
        answer = answer[:1900]  # bot text 2,000자 한도
        try:
            if channel:
                bot_send_text(self.client, self.cfg["bot_id"], answer, channel_id=channel)
            else:
                bot_send_text(self.client, self.cfg["bot_id"], answer, user_id=uid)
            _log(f"회신({name}): {answer[:80]}")
        except Exception as e:
            _log(f"회신 실패: {str(e)[:150]}")

    def log_message(self, *a):
        pass


if __name__ == "__main__":
    _log(f"bridge start :{PORT}")
    ThreadingHTTPServer(("127.0.0.1", PORT), Bridge).serve_forever()
