#!/usr/bin/env python3
"""신원 해석 — 코드에 사람을 박지 않기 위한 단일 창구.

왜 있나
  사람의 ID·이름·부서를 코드에 적으면 세 가지가 동시에 망가진다.
    1 공개 저장소에 개인 식별자가 남는다
    2 담당자가 바뀔 때마다 코드를 고쳐야 한다
    3 조직도와 코드가 따로 놀기 시작한다 — 어느 쪽이 맞는지 아무도 모른다

  이 조직에는 이미 정본이 둘 있다.
    · **조직도**(그룹웨어 디렉터리) — 재직·부서·직급·메일·사용자ID 의 정본
    · **온톨로지**(`00_공통/50_온톨로지/사람/`) — 조직도를 받아 적고 맥락을 더한 것

  그러므로 코드가 가질 것은 **"누구"가 아니라 "어떤 역할"** 하나뿐이다.
  역할 → 메일은 설정(`operator.account`)에 있고, 메일 → ID 는 조직도가 안다.

쓰는 법
    from lib.identity import operator_user_id, person
    uid = operator_user_id()          # 없으면 None — 보내는 쪽이 판단한다
    p   = person(email="...")         # 온톨로지 레코드 (이름·부서·직급)

못 찾으면
  **None 을 돌려주고 이유를 남긴다. 조용히 넘어가지 않는다.**
  알림 대상을 못 찾은 것은 "알림이 필요 없다"가 아니라 "알림이 안 나간다"이다.
"""
from __future__ import annotations

import json
import os
import re
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from lib.config import cfg, paths  # noqa: E402

CACHE_TTL = 24 * 3600          # 조직도는 자주 바뀌지 않는다
_MEM: dict[str, object] = {}


def _cache_path() -> Path:
    return paths.state / "identity.json"


def _load_cache() -> dict:
    p = _cache_path()
    try:
        d = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    if time.time() - d.get("_fetched", 0) > CACHE_TTL:
        return {}
    return d


def _save_cache(d: dict) -> None:
    p = _cache_path()
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        d["_fetched"] = time.time()
        p.write_text(json.dumps(d, ensure_ascii=False, indent=1), encoding="utf-8")
    except OSError:
        pass                                    # 캐시는 있으면 좋은 것 — 없다고 멈추지 않는다


def _directory() -> dict[str, dict]:
    """조직도 전체를 메일 기준으로 모은다. 호출 실패는 빈 dict — 예외를 올리지 않는다."""
    if "dir" in _MEM:
        return _MEM["dir"]                      # type: ignore[return-value]
    cached = _load_cache().get("by_email")
    if cached:
        _MEM["dir"] = cached
        return cached
    out: dict[str, dict] = {}
    try:
        sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "agents"))
        from nworks.client import NworksClient           # noqa: PLC0415
        from nworks.domains import directory_users       # noqa: PLC0415
        for u in directory_users(NworksClient()):
            email = (u.get("email") or "").strip().lower()
            if not email:
                continue
            nm = u.get("userName") or {}
            out[email] = {
                "user_id": u.get("userId") or u.get("userExternalKey") or "",
                "name": ((nm.get("lastName") or "") + (nm.get("firstName") or "")).strip(),
            }
    except Exception as e:                               # noqa: BLE001
        print(f"! 조직도 조회 실패: {type(e).__name__}: {e}", file=sys.stderr)
        return {}
    _MEM["dir"] = out
    _save_cache({"by_email": out})
    return out


def user_id_for(email: str | None) -> str | None:
    """메일 주소 → 그룹웨어 사용자 ID. 못 찾으면 None."""
    if not email:
        return None
    rec = _directory().get(email.strip().lower())
    return (rec or {}).get("user_id") or None


def operator_user_id() -> str | None:
    """운영자에게 1:1 로 보낼 대상 ID.

    순서: 설정에 직접 적힌 값 → 조직도 조회(`operator.account`).
    둘 다 없으면 None 과 함께 이유를 적는다 — 보내는 쪽이 채널 발송으로 돌리든 멈추든 정한다.
    """
    explicit = cfg("operator.user_id")
    if explicit:
        return str(explicit)
    acct = cfg("operator.account")
    if not acct:
        print("! operator.account 가 설정에 없다 — 1:1 알림 대상을 정할 수 없다",
              file=sys.stderr)
        return None
    uid = user_id_for(acct)
    if not uid:
        print(f"! 조직도에서 운영자를 찾지 못했다 ({acct}) — 1:1 알림이 나가지 않는다. "
              f"조직도에 있는 주소인지 확인하거나 operator.user_id 를 설정에 적는다",
              file=sys.stderr)
    return uid


def alert_target() -> tuple[str, str] | None:
    """알림을 어디로 보낼지 정한다 → ("user", id) 또는 ("channel", id).

    1:1 을 못 찾으면 부서 채널로 돌린다. **알림이 조용히 사라지는 것이 가장 나쁘다.**
    둘 다 없으면 None — 그때는 부르는 쪽이 로그에 남기고 넘어간다.
    """
    uid = operator_user_id()
    if uid:
        return ("user", uid)
    ch = cfg("channels.dept")
    if ch:
        print("! 1:1 대상을 못 찾아 부서 채널로 보낸다", file=sys.stderr)
        return ("channel", str(ch))
    return None


def targets(key: str = "calendar") -> list[dict]:
    """일정·브리지 등에서 다룰 사람 목록 → [{"email", "name"}, …]

    설정에는 **메일 주소만** 적는다. 이름은 조직도·온톨로지에서 읽는다 —
    사람 이름을 설정 파일에도, 코드에도 두 번 적지 않기 위해서다.
    비어 있으면 운영자 한 사람만 돌려준다. 빈 목록을 돌려주면 부르는 쪽이
    "대상이 없다"와 "설정을 안 했다"를 구분하지 못한다.
    """
    emails = cfg(f"access.{key}", None)
    if emails is None:
        emails = cfg("access.default", None)
    if isinstance(emails, str):
        emails = [e.strip() for e in emails.split(",")]
    if not emails:
        acct = cfg("operator.account")
        emails = [acct] if acct else []
    out = []
    d = _directory()
    for e in emails:
        e = str(e).strip().lower()
        if not e:
            continue
        name = (d.get(e) or {}).get("name") or ""
        if not name:
            rec = person(email=e)
            name = (rec or {}).get("title") or (rec or {}).get("_file", "").removesuffix(".md")
        out.append({"email": e, "name": name or e.split("@")[0]})
    return out


# ── 온톨로지 ────────────────────────────────────────────────────────────────
_FM = re.compile(r"^---\s*\n(.*?)\n---\s*\n", re.S)


def _people_dir() -> Path | None:
    root = paths.mount_root
    if not root:
        return None
    d = Path(root) / "00_공통/50_온톨로지/사람"
    return d if d.is_dir() else None


def person(email: str | None = None, name: str | None = None) -> dict | None:
    """온톨로지의 사람 레코드를 읽는다 (이름·부서·직급·메일).

    조직도가 ID 의 정본이라면, 온톨로지는 **맥락**의 정본이다 — 어느 과제에 붙어 있고
    누구와 주고받는지. 코드가 사람의 이름을 쓸 일이 있으면 여기서 읽는다.
    """
    d = _people_dir()
    if not d:
        return None
    want_email = (email or "").strip().lower()
    for p in d.glob("*.md"):
        if name and p.stem != name:
            continue
        try:
            m = _FM.match(p.read_text(encoding="utf-8", errors="replace"))
        except OSError:
            continue
        if not m:
            continue
        fm = {}
        for line in m.group(1).splitlines():
            if ":" in line and not line.startswith((" ", "-", "\t")):
                k, v = line.split(":", 1)
                fm[k.strip()] = v.strip().strip('"').strip("'")
        if name and p.stem == name:
            return fm | {"_file": p.name}
        if want_email and fm.get("email", "").lower() == want_email:
            return fm | {"_file": p.name}
    return None


if __name__ == "__main__":
    acct = cfg("operator.account")
    print(f"operator.account : {acct or '(설정 없음)'}")
    uid = operator_user_id()
    print(f"조회된 사용자 ID  : {'찾음 (' + str(len(uid)) + '자)' if uid else '못 찾음'}")
    t = alert_target()
    print(f"알림 대상        : {t[0] if t else '없음 — 알림이 나가지 않는다'}")
    pr = person(email=acct) if acct else None
    print(f"온톨로지 레코드   : {pr.get('_file') if pr else '못 찾음'}"
          + (f" · {pr.get('department')} · {pr.get('jobLevel')}" if pr else ""))
