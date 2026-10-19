"""A1 mail collector — 수집 계정의 받은편지함을 전량 수집한다.

수집 계정은 `~/.nworks/config.json` 의 `mail_accounts` 에 적힌 것뿐이고, 현재 `{{BOT_USER}}` 하나다
(운영자 결정 2026-09-04: {{OPERATOR_USER}} 개인계정 경유 폐지, research@ 그룹의 역할은 {{BOT_USER}} 이 대신한다).
주소 필터는 쓰지 않는다 (운영자 확정 2026-08-12: 전 수신 메일 수집).

Idempotent: state file tracks every seen mailId — 재실행 시 신규분만 조회.
Raw layout (nworks_cycle 이 U/5. Naverworks/00_raw/mail_<계정키>/ 로 동기화):
  output/raw_mail/<계정키>/<YYYY-MM>/<mailId>.json           (mail.get 응답 원문)
  output/raw_mail/<계정키>/<YYYY-MM>/<mailId>_att/<filename> (첨부 디코드본)
State:
  output/raw_mail/<계정키>/state.json
  {"seen": {mailId: "match"|"skip"}, "account": ...}

Read-only guarantee: uses only GET endpoints (list/get/attachment). No PATCH/DELETE —
읽음 상태도 건드리지 않는다.
"""
import base64
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "core"))
from nworks.client import NworksClient  # noqa: E402

INBOX_NAMES = ("받은메일함", "받은편지함", "Inbox", "INBOX")


def _safe_name(name: str) -> str:
    return re.sub(r'[/\\:*?"<>|\x00-\x1f]', "_", name)[:200] or "unnamed"


def find_inbox_folder(client: NworksClient) -> dict:
    body = client.call("mail.folders", path_params={"user_id": "me"})
    folders = body.get("mailFolders") or body.get("folders") or []
    for f in folders:
        if f.get("folderName") in INBOX_NAMES or f.get("folderType") == "receive":
            return f
    raise RuntimeError(f"받은편지함을 찾지 못함 — 폴더 응답 키 확인 필요: {json.dumps(body, ensure_ascii=False)[:400]}")


def scan_folders(client: NworksClient) -> list[dict]:
    """받은메일함 + 사용자 폴더(folderType U) 전부.

    받은메일함만 스캔하면 자동분류/수동정리로 사용자 폴더에 옮겨진 수신 메일을
    영영 못 본다(실측 2026-08-12: 받은메일함 4통 vs 연구기획사업부 폴더 558통).
    보낸·수신확인·임시·휴지통·스팸·메모 등 나머지 시스템 폴더는 제외.
    """
    body = client.call("mail.folders", path_params={"user_id": "me"})
    folders = body.get("mailFolders") or body.get("folders") or []
    out = [f for f in folders
           if f.get("folderName") in INBOX_NAMES or f.get("folderType") == "U"]
    if not out:
        raise RuntimeError(f"스캔할 폴더 없음 — 폴더 응답 확인 필요: {json.dumps(body, ensure_ascii=False)[:400]}")
    return out


def _recipients(mail_obj: dict) -> list[str]:
    out = []
    for field in ("to", "cc"):
        for r in mail_obj.get(field) or []:
            addr = r.get("email") if isinstance(r, dict) else str(r)
            if addr:
                out.append(addr.lower())
    return out


def _store(mail: dict, mail_obj: dict, mid: str, base: Path,
           client: NworksClient, received: str) -> None:
    month = (received or "0000-00")[:7]
    mdir = base / month
    mdir.mkdir(parents=True, exist_ok=True)
    (mdir / f"{mid}.json").write_text(json.dumps(mail, ensure_ascii=False, indent=1))
    # 실측: attachments는 mail 객체 내부가 아니라 응답 최상위에 온다 (2026-08-04)
    # inline(cid)은 data가 JSON에 내장돼 있어 별도 다운로드 불필요.
    # 대용량 첨부는 attachmentId<0 + bigFileExpireTime — 만료 시 404.
    atts = mail.get("attachments") or mail_obj.get("attachments") or []
    for att in atts:
        if att.get("cid") or att.get("contentDisposition") == "inline":
            continue
        try:
            data = client.call("mail.attachment", path_params={
                "user_id": "me", "mail_id": mid,
                "attachment_id": att["attachmentId"]})
            adir = mdir / f"{mid}_att"
            adir.mkdir(exist_ok=True)
            fname = _safe_name(data.get("filename") or att.get("filename", "unnamed"))
            (adir / fname).write_bytes(base64.b64decode(data["data"]))
        except Exception as e:  # 만료된 대용량(404)·서버 500 등은 기록 후 계속
            (mdir / f"{mid}_att_errors.log").open("a").write(
                f"{att.get('attachmentId')} {att.get('filename')}: {str(e)[:200]}\n")


# 광고 자동 필터 (운영자 확정 2026-08-12): "(광고)" 표기 메일은 수집 제외하고
# 발신 도메인을 차단 목록에 자동 등록 — 이후 표기가 없어도 그 도메인 발신은 제외.
# 정보통신망법상 광고 메일은 제목에 (광고) 표기가 의무라 마커 신뢰도가 높다.
AD_SUBJECT_RE = re.compile(r"^\s*[\[(（]\s*광고")
NEVER_BLOCK_DOMAINS = frozenset({"{{ORG_DOMAIN}}"})  # 사내 발신은 어떤 경우에도 차단하지 않음


def _blocklist_path(out_root: Path) -> Path:
    return out_root / "ad_block_domains.json"


def _load_blocklist(out_root: Path) -> set:
    p = _blocklist_path(out_root)
    return set(json.loads(p.read_text())) if p.exists() else set()


def _save_blocklist(out_root: Path, blocked: set) -> None:
    _blocklist_path(out_root).write_text(
        json.dumps(sorted(blocked), ensure_ascii=False, indent=1))


def _dept_addresses() -> frozenset:
    """부서 수신 메일로 볼 주소 — 설정에서 읽는다.

    여기에 주소를 박으면 공개 저장소에 구성원 계정이 남고, 사람이 바뀔 때마다
    코드를 고쳐야 한다. 둘 다 피한다.

    대표주소(research@) 하나로 걸러지지 않는 이유: 자동 포워딩이 헤더에 대표주소를
    남기지 않는 경우가 있다(게이트웨이가 숨긴다). 그래서 부서원 주소를 함께 본다.
    비어 있으면 거르지 않는다 — 조용히 0건이 되는 것보다 전부 가져오는 쪽이 낫다.
    """
    try:
        from lib.config import cfg                      # noqa: PLC0415
    except ImportError:
        return frozenset()
    vals = cfg("ssot.dept_addresses", []) or []
    if isinstance(vals, str):
        vals = [v.strip() for v in vals.split(",")]
    return frozenset(v.strip().lower() for v in vals if v and str(v).strip())


DEPT_ADDRESSES = _dept_addresses()


def collect(account: str, out_root: Path, max_new: int = 200,
            filter_address=DEPT_ADDRESSES) -> dict:
    """account 편지함을 스캔, filter 주소(집합/문자열) 수신분만 저장 (None이면 전부)."""
    client = NworksClient(ua_account=account)
    if isinstance(filter_address, str):
        filter_address = frozenset({filter_address})
    # 계정별 저장소 분리 — mailId는 계정(편지함) 내에서만 유일하므로 트리·state를 섞으면 충돌한다.
    # 2026-09-28: {{OPERATOR_USER}}→research 승계 분기 제거. {{OPERATOR_USER}} 수집은 2026-09-04 폐지됐고
    # 그 코퍼스(research/)는 8/12 에 멈춘 채 남아 "최신 메일" 로 오독되는 원인이 됐다.
    # 계정 키는 언제나 로컬파트 그대로다.
    fkey = account.split("@")[0]
    base = out_root / fkey
    base.mkdir(parents=True, exist_ok=True)
    state_path = base / "state.json"
    state = (json.loads(state_path.read_text()) if state_path.exists()
             else {"seen": {}, "account": account})
    seen = state["seen"]

    folders = scan_folders(client)
    new_ids = []
    for folder in folders:
        folder_id = folder.get("folderId")  # 받은메일함은 0 — falsy여도 유효한 ID
        if folder_id is None:
            folder_id = folder.get("mailFolderId")
        for m in client.paginate("mail.list", item_field="mails",
                                 path_params={"user_id": "me", "folder_id": folder_id},
                                 params={"count": 100}):
            mid = str(m["mailId"])
            if mid in seen:
                continue
            new_ids.append((mid, m.get("receivedTime", "")))
            if len(new_ids) >= max_new:
                break
        if len(new_ids) >= max_new:
            break

    matched = ads = 0
    blocked = _load_blocklist(out_root)
    want = ({a.lower() for a in filter_address} if filter_address else None)
    for mid, received in new_ids:
        mail = client.call("mail.get", path_params={"user_id": "me", "mail_id": mid})
        mail_obj = mail.get("mail") or mail
        sender = ((mail_obj.get("from") or {}).get("email") or "").lower()
        domain = sender.split("@")[-1] if "@" in sender else ""
        if domain and domain in blocked:
            seen[mid] = "ad_blocked"
            ads += 1
        elif AD_SUBJECT_RE.match(mail_obj.get("subject") or ""):
            seen[mid] = "ad"
            ads += 1
            if domain and domain not in NEVER_BLOCK_DOMAINS:
                blocked.add(domain)
                _save_blocklist(out_root, blocked)
        elif want is None or want & set(_recipients(mail_obj)):
            _store(mail, mail_obj, mid, base, client, received)
            seen[mid] = "match"
            matched += 1
        else:
            seen[mid] = "skip"
        state_path.write_text(json.dumps(state, ensure_ascii=False))  # 매건 저장 — 중단 내성

    return {"folders": [f.get("folderName") for f in folders],
            "scanned_new": len(new_ids), "matched": matched,
            "ads_filtered": ads, "blocked_domains": len(blocked),
            "total_seen": len(seen),
            "rate_remaining": client.last_rate_remaining}


if __name__ == "__main__":
    # 2026-10-01: 기본 계정을 {{OPERATOR_USER}}→{{BOT_USER}} 으로. nwctl 은 9/29 에 고쳤지만 이 직접 실행
    # 경로가 남아 있었다 — 인자 없이 돌리면 폐지된 개인계정을 수집하게 된다.
    account = sys.argv[1] if len(sys.argv) > 1 else "{{BOT_ACCOUNT}}"
    root = Path(__file__).resolve().parents[2] / "output" / "raw_mail"
    print(json.dumps(collect(account, root), ensure_ascii=False))
