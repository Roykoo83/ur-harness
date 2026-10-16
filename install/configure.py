#!/usr/bin/env python3
"""설치 구성기 — 설정값을 코드에 채우고 서비스 유닛을 만든다.

저장소 파일은 자리표시자(`{{TOKEN}}`)를 담고 있다. 공개 저장소이기 때문이다.
이 도구가 `config/harness.yaml` 을 읽어 **build/ 에 실행본을 만든다.**
저장소 원본은 건드리지 않는다 — 그래서 `git status` 가 깨끗하게 유지된다.

    python3 install/configure.py --check     설정이 다 찼는지 본다
    python3 install/configure.py --build     build/ 에 실행본을 만든다
    python3 install/configure.py --units     서비스 유닛을 만든다
    python3 install/configure.py --all       위 셋을 순서대로

담당자가 바뀌면 `config/harness.yaml` 의 `operator:` 블록만 고치고 다시 돌린다.
"""
from __future__ import annotations

import argparse
import os
import platform
import re
import shutil
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "core"))
from lib.config import cfg, load, missing  # noqa: E402

BUILD = REPO / "build"
COPY_DIRS = ["core", "agents", "shared", "tasks", "skills"]
COPY_EXT = {".py", ".sh", ".md", ".json", ".yaml", ".yml", ".mjs", ".js", ".html", ".txt", ".tsv"}
TOKEN = re.compile(r"\{\{([A-Z_]+)\}\}")


def _is_substitutable(src: Path) -> bool:
    """치환 대상인가. 확장자만 보지 않는다.

    `nwctl` 처럼 확장자 없는 실행 스크립트를 확장자로만 거르면
    토큰이 치환되지 않은 채 build/ 로 복사되어 실행 시점에 깨진다.
    스캐너와 같은 함정이다 — 같은 방식으로 막는다.
    """
    if src.suffix.lower() in COPY_EXT:
        return True
    try:
        head = src.open("rb").read(2048)
    except OSError:
        return False
    if not head or b"\0" in head:
        return False
    if not head.startswith(b"#!"):
        return False
    try:
        head.decode("utf-8")
    except UnicodeDecodeError:
        return False
    return True


def _local(addr: str | None) -> str:
    return (addr or "").split("@")[0]


def _host(url: str | None) -> str:
    return re.sub(r"^https?://", "", url or "").rstrip("/")


def mapping() -> dict[str, str]:
    """토큰 → 실제 값. 설정에 없으면 빈 문자열이 아니라 토큰을 남겨 둔다."""
    ws = Path(cfg("operator.workspace", "~/ur-harness-work")).expanduser()
    mount = cfg("operator.mount_root", "")
    members = cfg("members", []) or []

    m = {
        "ORG_NAME":         cfg("org.name"),
        "ORG_DOMAIN":       cfg("org.domain"),
        "ORG_SLUG":         (cfg("org.domain") or "").split(".")[0] or None,
        "WIKI_BASE":        cfg("org.wiki_base"),
        "WIKI_HOST":        _host(cfg("org.wiki_base")) or None,
        "SSOT_DRIVE":       cfg("org.drives.ssot.name"),
        "SSOT_DRIVE_ID":    cfg("org.drives.ssot.id"),
        "SOURCE_DRIVE":     cfg("org.drives.source.name"),
        "SOURCE_DRIVE_ID":  cfg("org.drives.source.id"),
        "BOT_ACCOUNT":      cfg("org.accounts.bot"),
        "BOT_USER":         _local(cfg("org.accounts.bot")) or None,
        "SERVICE_ACCOUNT":  cfg("org.accounts.service"),
        "OPERATOR_ACCOUNT": cfg("operator.account"),
        "OPERATOR_USER":    _local(cfg("operator.account")) or None,
        "OPERATOR_NAME":    cfg("operator.name"),
        "WORKSPACE_NAME":   ws.name,
        "WORKSPACE_SLUG":   ws.name.lower().replace("_", "-"),
        "HOME":             str(Path.home()),
        "MOUNT_ROOT":       mount or str(ws),
        "MOUNT_REL":        (mount.split(str(Path.home()) + "/")[-1] if mount else ""),
        "DEPT_ROOT":        cfg("ssot.dept_root"),
    }
    for i, letter in enumerate("ABCDE"):
        m[f"MEMBER_{letter}"] = members[i] if i < len(members) else f"구성원 {letter}"
    return {k: v for k, v in m.items() if v is not None}


# 토큰을 손으로 이어 붙이면 존재하지 않는 값이 만들어진다.
# `{{BOT_USER}}@{{ORG_NAME}}.co.kr` 은 표시이름에 도메인을 붙인 것이라 주소가 아니다.
# 완성된 토큰(BOT_ACCOUNT · OPERATOR_ACCOUNT)이 따로 있으므로 그것을 쓴다.
BAD_ASSEMBLY = re.compile(
    r"\{\{(?:ORG_NAME|ORG_SLUG)\}\}\s*\.[A-Za-z]{2,}"      # 표시이름 + 도메인 접미사
    r"|\{\{[A-Z_]*USER\}\}@\{\{(?:ORG_NAME|ORG_SLUG)\}\}"  # 계정 + 표시이름
)


def _assembly_errors() -> list[str]:
    out = []
    for d in COPY_DIRS:
        for p in (REPO / d).rglob("*"):
            if not p.is_file() or "__pycache__" in p.parts or not _is_substitutable(p):
                continue
            try:
                lines = p.read_text(encoding="utf-8", errors="replace").splitlines()
            except OSError:
                continue
            for i, line in enumerate(lines, 1):
                if BAD_ASSEMBLY.search(line):
                    out.append(f"{p.relative_to(REPO)}:{i}")
    return out


def cmd_check() -> int:
    miss = missing()
    mp = mapping()
    bad = _assembly_errors()
    used: set[str] = set()
    for d in COPY_DIRS:
        for p in (REPO / d).rglob("*"):
            if p.is_file() and "__pycache__" not in p.parts and _is_substitutable(p):
                try:
                    used |= set(TOKEN.findall(p.read_text(encoding="utf-8", errors="replace")))
                except OSError:
                    pass
    unresolved = sorted(used - set(mp))
    print(f"설정 블록 {len(load())}개 · 코드가 쓰는 토큰 {len(used)}종")
    if miss:
        print(f"\n아직 자리표시자인 설정 {len(miss)}개:")
        for k in miss:
            print(f"  · {k}")
    if unresolved:
        print(f"\n값이 없는 토큰 {len(unresolved)}종 — 이대로 build 하면 그대로 남는다:")
        for t in unresolved:
            print(f"  · {{{{{t}}}}}")
    if bad:
        print(f"\n토큰을 이어 붙인 곳 {len(bad)}곳 — 존재하지 않는 값이 만들어진다:")
        for b in bad:
            print(f"  · {b}")
        print("  완성 토큰을 쓴다: {{BOT_ACCOUNT}} · {{OPERATOR_ACCOUNT}} · {{ORG_DOMAIN}}")
    if not miss and not unresolved and not bad:
        print("\n✓ 설정이 다 찼다 — build 해도 된다")
        return 0
    print("\n config/harness.yaml 을 채운 뒤 다시 확인한다")
    return 1


def cmd_build() -> int:
    mp = mapping()
    if BUILD.exists():
        shutil.rmtree(BUILD)
    n_files = n_subs = 0
    for d in COPY_DIRS:
        src_root = REPO / d
        if not src_root.is_dir():
            continue
        for src in src_root.rglob("*"):
            if not src.is_file() or "__pycache__" in src.parts:
                continue
            dst = BUILD / src.relative_to(REPO)
            dst.parent.mkdir(parents=True, exist_ok=True)
            if not _is_substitutable(src):
                shutil.copy2(src, dst)
                n_files += 1
                continue
            try:
                text = src.read_text(encoding="utf-8")
            except UnicodeDecodeError:
                shutil.copy2(src, dst)
                n_files += 1
                continue
            new, cnt = TOKEN.subn(lambda m: mp.get(m.group(1), m.group(0)), text)
            dst.write_text(new, encoding="utf-8")
            if src.suffix == ".sh" or new.startswith("#!"):
                dst.chmod(0o755)
            n_files += 1
            n_subs += cnt
    (BUILD / "config").mkdir(parents=True, exist_ok=True)
    for name in ("harness.yaml", "harness.yml", "harness.json"):
        p = REPO / "config" / name
        if p.is_file():
            shutil.copy2(p, BUILD / "config" / name)
            break
    left = 0
    for p in BUILD.rglob("*"):
        if p.is_file() and _is_substitutable(p):
            try:
                left += len(TOKEN.findall(p.read_text(encoding="utf-8", errors="replace")))
            except OSError:
                pass
    print(f"build/ 에 {n_files}개 파일 · 토큰 {n_subs}곳 치환")
    print(f"  남은 자리표시자: {left}곳" + ("  ✓" if left == 0 else "  ← --check 로 확인한다"))
    return 0 if left == 0 else 1


UNIT_SERVICE = """[Unit]
Description=UR Harness — {desc}

[Service]
Type=oneshot
WorkingDirectory={build}
Environment=PYTHONPATH={build}/core:{build}/shared
ExecStart={python} {build}/{exec_path}
StandardOutput=append:{logs}/{name}.log
StandardError=append:{logs}/{name}.log
"""

UNIT_DAEMON = """[Unit]
Description=UR Harness — {desc}
After=network-online.target

[Service]
Type=simple
WorkingDirectory={build}
Environment=PYTHONPATH={build}/core:{build}/shared
ExecStart={python} {build}/{exec_path}
Restart=always
RestartSec=20
StandardOutput=append:{logs}/{name}.log
StandardError=append:{logs}/{name}.log

[Install]
WantedBy=default.target
"""

UNIT_TIMER = """[Unit]
Description=UR Harness timer — {desc}

[Timer]
{spec}
Persistent=true
Unit={name}.service

[Install]
WantedBy=timers.target
"""


def cmd_units(with_optional: list[str], all_optional: bool) -> int:
    try:
        import yaml
    except ImportError:
        print("pyyaml 이 필요하다: pip install pyyaml", file=sys.stderr)
        return 2
    defs = yaml.safe_load((REPO / "install" / "services.yaml").read_text(encoding="utf-8"))
    out = REPO / "install" / "systemd"
    out.mkdir(parents=True, exist_ok=True)
    for old in out.glob("ur-*"):
        old.unlink()
    logs = Path(cfg("operator.workspace", "~/ur-harness-work")).expanduser() / "logs"
    python = shutil.which("python3") or "/usr/bin/python3"
    host_os = platform.system().lower()

    made, skipped, broken = [], [], []
    for svc in defs.get("services", []):
        name, sched = svc["name"], svc.get("schedule", {})
        if not (REPO / svc["exec"]).is_file():
            broken.append(f"{name} → {svc['exec']}")
            continue
        plats = svc.get("platform")
        if plats and host_os not in [p.lower() for p in plats]:
            skipped.append(f"{name} (플랫폼)")
            continue
        if svc.get("optional") and not all_optional and name not in with_optional:
            skipped.append(f"{name} (선택)")
            continue
        unit = f"ur-{name}"
        # .sh 를 python 으로 실행하면 유닛이 즉시 죽는다. 확장자로 실행기를 고른다
        ex = svc["exec"]
        runner = "/bin/bash" if ex.endswith((".sh", ".bash")) else (
            "/usr/bin/env node" if ex.endswith((".js", ".mjs")) else python)
        ctx = {"desc": svc.get("desc", name), "build": BUILD, "python": runner,
               "exec_path": ex, "logs": logs, "name": unit}
        if sched.get("keepalive"):
            (out / f"{unit}.service").write_text(UNIT_DAEMON.format(**ctx), encoding="utf-8")
        else:
            (out / f"{unit}.service").write_text(UNIT_SERVICE.format(**ctx), encoding="utf-8")
            if "every" in sched:
                spec = f"OnBootSec=2min\nOnUnitActiveSec={int(sched['every'])}s"
            else:
                spec = f"OnCalendar={sched['calendar']}"
            (out / f"{unit}.timer").write_text(
                UNIT_TIMER.format(spec=spec, **ctx), encoding="utf-8")
        made.append(unit)

    if broken:
        print(f"✗ 실행 파일이 없는 서비스 {len(broken)}개 — 유닛을 만들지 않았다:")
        for b in broken:
            print(f"  · {b}")
        print("  install/services.yaml 의 exec 경로를 고친다\n")
    print(f"systemd 유닛 {len(made)}개 → install/systemd/")
    for m in made:
        print(f"  · {m}")
    if skipped:
        print(f"\n건너뜀 {len(skipped)}개: {', '.join(skipped)}")
        print("  선택 서비스를 켜려면: --with <이름>  또는  --with-optional")
    print(f"\n설치:\n  mkdir -p ~/.config/systemd/user {logs}\n"
          f"  cp install/systemd/ur-*.{{service,timer}} ~/.config/systemd/user/\n"
          f"  systemctl --user daemon-reload\n"
          f"  for t in ~/.config/systemd/user/ur-*.timer; do "
          f"systemctl --user enable --now \"$(basename \"$t\")\"; done\n"
          f"  loginctl enable-linger \"$USER\"      # 로그아웃해도 돌게")
    return 1 if broken else 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--build", action="store_true")
    ap.add_argument("--units", action="store_true")
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--with", dest="with_opt", action="append", default=[],
                    help="선택 서비스를 켠다 (여러 번 가능)")
    ap.add_argument("--with-optional", action="store_true", help="선택 서비스를 전부 켠다")
    a = ap.parse_args()
    if not (a.check or a.build or a.units or a.all):
        ap.print_help()
        return 0
    rc = 0
    if a.check or a.all:
        rc |= cmd_check()
        # 점검이 실패했는데 빌드를 이어 가면, 자리표시자가 그대로 남은 build/ 가 만들어진다.
        # 설치하는 사람은 그것이 완성본인 줄 알고 서비스를 올린다 — 거기서 멈추는 쪽이 낫다.
        if rc and a.all:
            print("\n점검을 통과하지 못해 빌드를 멈춘다. "
                  "config/harness.yaml 을 채우고 다시 돌린다.")
            return rc
    if a.build or a.all:
        rc |= cmd_build()
    if a.units or a.all:
        rc |= cmd_units(a.with_opt, a.with_optional)
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
