# NAVER WORKS API endpoint table — single source for all domain calls.
# Verified against research/api_reference.md (2026-08-03, official docs quoted).
# verified=True means the path/method/params were reconciled with the reference;
# usage gates (UA-only, approval) are enforced by RULES, not this flag.

from __future__ import annotations

AUTH_HOST = "https://auth.worksmobile.com"
API_HOST = "https://www.worksapis.com"  # confirmed

TOKEN_URL = f"{AUTH_HOST}/oauth2/v2.0/token"          # confirmed
AUTHORIZE_URL = f"{AUTH_HOST}/oauth2/v2.0/authorize"  # confirmed (UA flow, state required)

V = "/v1.0"

ENDPOINTS = {
    # domain.key: (method, path_template, required_scope, verified)
    # Bot — text max 2,000자, 201 응답
    "bot.send_user":    ("POST", V + "/bots/{bot_id}/users/{user_id}/messages", "bot.message", True),
    "bot.send_channel": ("POST", V + "/bots/{bot_id}/channels/{channel_id}/messages", "bot.message", True),
    # 파일 전송 3단계: attachments 로 uploadUrl·fileId 발급 → uploadUrl 에 multipart(Filedata) 업로드
    # → messages 에 {"content":{"type":"file","fileId":...}} 전송. [2026-09-21 실측 200/200/201]
    "bot.attachment":   ("POST", V + "/bots/{bot_id}/attachments", "bot.message", True),
    # Directory — users: count 1~100, orderBy/sortOrder
    "directory.users":  ("GET",  V + "/users", "directory.read", True),
    "directory.user":   ("GET",  V + "/users/{user_id}", "directory.read", True),
    "directory.orgunits": ("GET", V + "/orgunits", "directory.read", True),  # domainId 필수 param
    # Calendar — 목록은 calendar-personals(count 1~50); events는 커서 없음,
    # fromDateTime/untilDateTime 최대 31일, '+'는 %2B 인코딩
    "calendar.list":    ("GET",  V + "/users/{user_id}/calendar-personals", "calendar.read", True),
    "calendar.events":  ("GET",  V + "/users/{user_id}/calendars/{calendar_id}/events", "calendar.read", True),
    # 기본 캘린더 조회·일정 생성. 생성은 쓰기 스코프 `calendar` 필요 (calendar.read 로는 403)
    "calendar.default": ("GET",  V + "/users/{user_id}/calendar", "calendar.read", True),
    "calendar.create":  ("POST", V + "/users/{user_id}/calendar/events", "calendar", True),
    # Mail — UA 토큰 전용(SA 불가). userId는 'me' 허용. 첨부는 Base64 JSON(최대 25MB)
    "mail.folders":     ("GET",  V + "/users/{user_id}/mail/mailfolders", "mail.read", True),
    "mail.list":        ("GET",  V + "/users/{user_id}/mail/mailfolders/{folder_id}/children", "mail.read", True),
    "mail.get":         ("GET",  V + "/users/{user_id}/mail/{mail_id}", "mail.read", True),
    "mail.attachment":  ("GET",  V + "/users/{user_id}/mail/{mail_id}/attachments/{attachment_id}", "mail.read", True),
    # Audit — 동기식 GET, 302 location 헤더에 CSV URL. 기간 최대 31일 · 동시 1건 · 10 req/min
    "audit.download":   ("GET",  V + "/audits/logs/download", "audit.read", True),
}

# 클라이언트 특례: audit.download는 redirect를 따라가지 않고 location을 반환해야 한다.
REDIRECT_KEYS = {"audit.download"}
# 커서 페이지네이션 미지원이 확인된 목록 API (paginate 사용 금지)
NO_CURSOR_KEYS = {"calendar.events"}
