#!/usr/bin/env python3
"""연구기획사업부 대시보드를 SSOT 공유드라이브에 배포한다.

    python3 scripts/publish_dashboards.py

배포 방식
    **폴더째 그대로 올린다.** 구글 드라이브를 로컬에 마운트해 쓰므로(Drive for Desktop),
    받는 사람은 드라이브 폴더에서 `index.html` 을 더블클릭하면 된다.
    실제 파일시스템이라 페이지 간 링크와 이미지가 그대로 동작한다.
    (웹 드라이브에서 직접 열면 렌더링되지 않는다 — 로컬 마운트로 열어야 한다.)

배포 위치
    {{SSOT_DRIVE}}/{{DEPT_ROOT}}/50_대시보드/
    전사 공통이 아니라 **연구기획사업부가 만들고 운영하는 시스템**이다.
"""
from __future__ import annotations

import datetime as dt
import shutil
import subprocess
import sys
import tempfile
import unicodedata
from pathlib import Path

# ---- 클라우드 정본 업로드 (rclone · Drive API 직행)
#
# 로컬 마운트 쓰기는 FileProvider→DriveFS 4단계 비동기 체인이라 조용히 유실된다
# (2026-09-03~04 실측: 24시간 업로드 정지, 부서원 전원이 산출물을 못 봄 — E24·E26).
# 그래서 **클라우드 반영은 rclone 이 정본 경로**이고, 로컬 마운트 복사는 이 맥에서
# 바로 열어보기 위한 부수 사본이다. rclone 이 없거나 실패하면 경고만 남긴다 (비치명).
# 2026-09-05: 서비스계정 원격(SSOT_rw)으로 이전. gdrive(rclone 공용 client_id)는 분당 쿼터
# 상시 포화로 403 이 반복되고 2026년 중 폐기 예고됐다. lib/ssot_io 가 폴백까지 처리한다.
RCLONE_REMOTE = "gdrive,team_drive={{SSOT_DRIVE_ID}}:"   # 폴백용(직접 사용 안 함)
RCLONE = None            # 쓰기 직전에 _rclone() 로 찾는다

def _rclone() -> str | None:
    """rclone 경로. 못 찾으면 None — 「없음」을 가짜 경로로 숨기지 않는다."""
    import sys as _s
    from pathlib import Path as _P
    _s.path.insert(0, str(_P(__file__).resolve().parents[1] / "core"))
    from lib.runtime import find_exe                          # noqa: PLC0415
    return find_exe("rclone", env_var="RCLONE_BIN")


def _P(name: str, sub: str = ""):
    """표준 경로 — 설정이 정한 작업공간 아래. 예전 형제-프로젝트 경로를 대신한다."""
    import sys as _s
    from pathlib import Path as _Path
    _s.path.insert(0, str(_Path(__file__).resolve().parents[1] / "core"))
    from lib.config import paths                                 # noqa: PLC0415
    if name == "raw":
        return paths.raw(sub)
    v = getattr(paths, name)
    return (v / sub) if sub else v


def cloud_put(src: Path, rel_dst: str) -> bool:
    """SSOT 로 올리고 **해시 대조로 도착까지 검증**한다. 업로드 rc=0 은 도착의 증거가 아니다."""
    import sys as _s
    _s.path.insert(0, str(Path(__file__).resolve().parent))
    from lib.ssot_io import put as _put                     # noqa: PLC0415
    ok, msg = _put(src, rel_dst)
    if not ok:
        print(f"  ! 클라우드 반영 실패 {rel_dst}: {msg}")
    return ok


def nfc(s: str) -> str:
    """드라이브 파일명은 NFD 로 올라오기도 한다. 비교 전에 정규화한다."""
    return unicodedata.normalize("NFC", s)

HOME = Path.home()
REPO = HOME / "{{WORKSPACE_NAME}}"
# 2026-09-07 (M 안, 구역표): 50_대시보드 는 **기계 구역 · API 단일 쓰기**다.
# 그 전까지는 마운트에 복사(shutil)하고 그 마운트 경로를 다시 rclone 으로 올렸다 — 같은 파일을
# DriveFS 와 API 가 같은 초에 두 번 올려 Drive 에 동명 파일 8쌍이 생겼다 (13:11:28 실측).
# 이제 마운트는 건드리지 않는다. 로컬 스테이지에 조립해 API 로만 sync 하고, 마운트에는
# DriveFS 가 알아서 내려준다.
DEST = REPO / "state" / "dash_stage"                 # 로컬 스테이지 (마운트 아님)
DEST_REL = "{{DEPT_ROOT}}/50_대시보드"           # 클라우드 목적지 (구역표: 기계)

# 작업용 파일은 배포하지 않는다. `_원본`은 빌드 전 원자료, `_통합`은 한 파일로 묶은 사본이라
# 진입점이 둘로 갈려 혼란만 준다 — 여기서는 index.html 하나로 들어간다.
SKIP = ("_원본.html", "_통합.html", "_draft.html")

BOARDS = [
    {"이름": "RPBD 운영 OS", "폴더": "10_운영OS",
     "src": _P("output"),
     # 태스크 상세는 대시보드에서 링크로만 들어간다. 같이 올려야 그 링크가 산다.
     "포함": ["rpbd_운영OS_대시보드.html", "태스크_상세.html"],
     "진입": "rpbd_운영OS_대시보드.html",
     "설명": "주간 의사결정 — 수행중·지원중·기타 과제 진행사항과 이슈, 회계·RA",
     "build": None},
    # 2026-09-14: 소스를 SSOT `50_도구상자/20_소스/` 로 옮겼다. 부서원도 같은 소스를 고치고
    # `dash` 스킬로 빌드한다. 이 맥의 로컬 사본으로 빌드하면 부서원 작업을 덮어쓴다
    # (2026-08~09 에 nipa·kdca·kstartup 이 그렇게 유실됐다). **반드시 SSOT 소스를 읽는다.**
    #
    # 2026-09-21: **자동 대상에서 뺐다.** 이 보드의 주인은 {{MEMBER_A}} 연구원이고,
    # 그의 노트북에서 `scripts/배포.py` 로 만들고 올린다. 이 맥이 같은 폴더를 계속
    # 만들면 「이 화면 누가 만든 거지」에서 다음 사람이 헤맨다.
    # 필요할 때만 손으로 부른다:  python3 scripts/publish_dashboards.py --보드 20_정부과제시스템
    {"이름": "정부과제 시스템 매뉴얼", "폴더": "20_정부과제시스템",
     "src": REPO / "state" / "dash_src" / "gov-system-manuals" / "output",
     "포함": None, "진입": "index.html",
     "설명": "PMS·RCMS·e나라도움 등 사업비 집행 시스템 사용법 · 계좌·카드 · 참여인력·인건비",
     "build": "ssot_gov",
     # 주인이 따로 있는 보드. 부르면 돌지만 기본으로는 돌지 않는다
     "자동": False, "주인": "{{MEMBER_A}} (연구기획사업부)"},
]


SSOT_GOV_SRC = "50_도구상자/20_소스/gov-system-manuals"


def _build_ssot_gov(b) -> bool:
    """SSOT 의 소스를 내려받아 빌드한다. 부서원과 같은 소스를 본다는 것이 핵심이다."""
    work = REPO / "state" / "dash_src" / "gov-system-manuals"
    work.mkdir(parents=True, exist_ok=True)
    g = subprocess.run([(RCLONE or _rclone()), "copy", f"SSOT_ro:{SSOT_GOV_SRC}", str(work),
                        "--exclude", "__pycache__/**"], capture_output=True, text=True)
    if g.returncode != 0:
        print(f"  ✗ SSOT 소스 내려받기 실패: {(g.stderr or '')[:200]}")
        return False
    out = work / "output"
    r = subprocess.run([sys.executable, str(work / "scripts" / "build.py"),
                        "--data", str(work / "data"), "--out", str(out)],
                       capture_output=True, text=True)
    if r.returncode != 0:
        print(f"  ✗ 빌드 실패: {(r.stderr or '')[:200]}")
        return False
    return out.exists()


def build(b) -> bool:
    if b["build"] == "ssot_gov":
        return _build_ssot_gov(b)
    if b["build"]:
        r = subprocess.run(b["build"], cwd=b["src"].parent, capture_output=True, text=True)
        if r.returncode != 0:
            print(f"  ✗ 빌드 실패: {(r.stderr or '')[:200]}")
            return False
    return b["src"].exists()


def _prune(folder: Path, keep: set[str]) -> None:
    """폴더는 그대로 두고, 원본에 없는 파일만 지운다."""
    if not folder.is_dir():
        return
    for f in folder.iterdir():
        if f.is_file() and nfc(f.name) not in keep:
            try:
                f.unlink()
            except OSError as e:
                print(f"  ! 잔여 파일 삭제 실패: {f.name} ({e})")


def copy_board(b) -> tuple[int, float]:
    # **폴더를 지우지 않는다.** Drive for Desktop 은 폴더 삭제 후 재생성을 충돌로 보고
    # `폴더 2`·`폴더 3` 사본을 만든다 (2026-09-03 실측: 운영OS·정부과제시스템 각 2개).
    # 파일 단위 덮어쓰기 + 잔여 파일 정리로 같은 결과를 얻으면서 충돌 사본을 만들지 않는다.
    dst = DEST / b["폴더"]
    dst.mkdir(parents=True, exist_ok=True)
    n, size = 0, 0
    names = b["포함"] or [f.name for f in b["src"].iterdir()
                          if f.suffix == ".html" and not f.name.endswith(SKIP)]
    copied = set()
    for name in names:
        f = b["src"] / name
        if not f.exists():
            continue
        shutil.copy2(f, dst / name)
        copied.add(nfc(name))
        n += 1
        size += f.stat().st_size
    _prune(dst, copied | {"img"})

    img = b["src"] / "img"
    if img.is_dir():
        shutil.copytree(img, dst / "img", dirs_exist_ok=True)
        _prune(dst / "img", {nfc(f.name) for f in img.iterdir() if f.is_file()})
        for f in (dst / "img").iterdir():
            n += 1
            size += f.stat().st_size
    return n, size / 1024


def cloud_sync(stage: Path, rel_dst: str) -> bool:
    """생성물 폴더는 원본이 곧 전체다 — copy 가 아니라 sync. 동명 파일 정리 → sync → 해시검증."""
    import sys as _s
    _s.path.insert(0, str(Path(__file__).resolve().parent))
    from lib.ssot_io import sync_generated as _sync          # noqa: PLC0415
    ok, msg = _sync(stage, rel_dst)
    if not ok:
        print(f"  ! 클라우드 sync 실패 {rel_dst}: {msg}")
    return ok


def main() -> int:
    import argparse                                        # noqa: PLC0415
    ap = argparse.ArgumentParser(description="연구기획사업부 대시보드 배포")
    ap.add_argument("--보드", dest="board", action="append", metavar="폴더",
                    help="이 보드만 배포한다. 주인이 있는 보드는 이렇게 불러야 돈다")
    a = ap.parse_args()

    if a.board:
        todo = [b for b in BOARDS if b["폴더"] in a.board or b["이름"] in a.board]
        unknown = [x for x in a.board
                   if not any(x in (b["폴더"], b["이름"]) for b in BOARDS)]
        for x in unknown:
            print(f"!! 그런 보드가 없습니다: {x}")
        if not todo:
            print("배포할 것이 없습니다. 고를 수 있는 보드:")
            for b in BOARDS:
                주 = f"  · 주인 {b['주인']}" if b.get("주인") else ""
                print(f"   {b['폴더']:20} {b['이름']}{주}")
            return 1
    else:
        todo = [b for b in BOARDS if b.get("자동", True)]
        skipped = [b for b in BOARDS if not b.get("자동", True)]
        for b in skipped:
            print(f"[{b['이름']}] 건너뜀 — 주인 {b.get('주인', '?')}. "
                  f"필요하면  --보드 {b['폴더']}")

    DEST.mkdir(parents=True, exist_ok=True)
    rows = []
    for b in todo:
        print(f"[{b['이름']}]")
        if not build(b):
            print("  ✗ 원본 없음 — 건너뜀")
            continue
        n, kb = copy_board(b)
        cloud = cloud_sync(DEST / b["폴더"], f"{DEST_REL}/{b['폴더']}")
        print(f"  → {b['폴더']}/  파일 {n}개  {kb:,.0f}KB  {'☁ 클라우드 반영' if cloud else ''}")
        rows.append((b, n, kb))

    now = dt.datetime.now()
    total = sum(n for _, n, _ in rows)
    idx = [
        "---",
        'path: "{{DEPT_ROOT}}/50_대시보드"',
        f"files_subtree: {total}",
        'sensitivity_max: "internal"',
        f"generated: {now:%Y-%m-%dT%H:%M:%S+0900}",
        'stale_after: "P7D"',
        "---",
        "",
        "## 이 폴더는",
        "",
        "연구기획사업부가 만들고 운영하는 **대시보드**다. 호스팅 서버가 없어 웹으로 띄우지 못하므로",
        "여기에 페이지를 그대로 올려 둔다.",
        "",
        "## 보는 법",
        "",
        "**구글 드라이브를 로컬에 연결(Drive for Desktop)한 상태에서 폴더를 열고,**",
        "각 대시보드의 진입 파일을 **더블클릭**하면 브라우저에서 열린다.",
        "",
        "> 웹 브라우저의 드라이브 화면에서 바로 열면 렌더링되지 않는다.",
        "> 구글 드라이브는 HTML을 미리보기하지 않기 때문이며, **로컬 드라이브 폴더로 열어야** 한다.",
        "",
        "## 대시보드",
        "",
        "| 대시보드 | 폴더 | 열 파일 | 내용 |",
        "|---|---|---|---|",
    ]
    for b, n, kb in rows:
        idx.append(f"| **{b['이름']}** | `{b['폴더']}/` | `{b['진입']}` | {b['설명']} |")
    idx += [
        "",
        "## 갱신",
        "",
        "원본이 바뀌면 `{{WORKSPACE_NAME}}/scripts/publish_dashboards.py` 를 다시 돌린다.",
        "빌드부터 배포·색인 갱신까지 한 번에 처리된다. **폴더는 지우지 않고 파일만 덮어쓰며**,",
        "원본에서 없어진 페이지는 배포 때 함께 정리된다.",
        "",
        "## 취급 주의",
        "",
        "**참여인력·인건비 페이지에는 임직원 이름과 과제별 참여율이 들어 있다.**",
        "사내 열람용이며 외부로 내보내지 않는다.",
        "",
        "## 여기 없는 것",
        "",
        "| 찾는 것 | 실제 위치 |",
        "|---|---|",
        "| 운영 OS 원본·데이터·생성 스크립트 | `{{WORKSPACE_NAME}}-work` |",
        "| 정부과제 시스템 소스·빌드 도구 | SSOT `50_도구상자/20_소스/gov-system-manuals` "
        "(담당 {{MEMBER_A}}) |",
        "| 과제별 예산·집행 원본 | `U/2. R&D 사업`, `U/4. R&D 예산_통합` |",
    ]
    (DEST / "_INDEX.md").write_text("\n".join(idx) + "\n", encoding="utf-8")
    cloud_put(DEST / "_INDEX.md", f"{DEST_REL}/_INDEX.md")

    # 동기화 하트비트 — 부서원이 자기 클로드로 "이 파일의 시각"을 물어 연동을 자가진단한다.
    # **API로 직접 올린 뒤 API로 재조회**해야 진짜 왕복 검증이다. 로컬 마운트에 쓰고
    # xattr 를 믿는 방식은 좀비 동기화를 못 잡았다 (E24).
    hb_text = (
        f"# 드라이브 동기화 확인\n\n"
        f"- 마지막 갱신: **{now:%Y-%m-%d %H:%M}** (3시간 주기 자동 · API 직행 업로드)\n"
        f"- 지금 시각과 3시간 넘게 차이 나면: 게시 시스템 문제입니다. {{OPERATOR_NAME}} 책임에게 알려주세요.\n"
        f"- 이 파일 자체가 안 보이면: 본인 컴퓨터의 Drive for Desktop 동기화 꺼짐(앱 실행),"
        f" 클로드 폴더 연결, 또는 공유 드라이브 권한 문제입니다.\n")
    with tempfile.NamedTemporaryFile("w", suffix=".md", delete=False,
                                     encoding="utf-8") as tf:
        tf.write(hb_text)
        tmp = Path(tf.name)
    hb_ok = cloud_put(tmp, "{{DEPT_ROOT}}/_동기화_확인.md")
    tmp.unlink(missing_ok=True)
    print(f"\n· _INDEX.md·하트비트 갱신{'(☁)' if hb_ok else '(!클라우드 실패)'} · "
          f"대시보드 {len(rows)}개 · 파일 {total}개 → {DEST}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
