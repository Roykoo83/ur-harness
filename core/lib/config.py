#!/usr/bin/env python3
"""설정 단일 창구 — 코드에 조직 고유값을 박지 않기 위한 로더.

왜 있나
  이 저장소는 공개된다. 그래서 도메인·드라이브 ID·계정 같은 조직 고유값이
  코드에 들어가면 안 된다. 값은 설정 파일에 두고 코드는 **이름으로 부른다.**

  운영상으로는 값이 바뀌지 않는다 — 같은 드라이브, 같은 봇, 같은 조직이다.
  **담당자가 바뀔 때 고치는 곳은 `operator:` 블록 하나뿐이다.**

읽는 순서 (뒤가 앞을 덮는다)
  1 config/harness.example.yaml   자리표시자. 키 목록의 기준
  2 config/harness.yaml|json      실제 값 (저장소에 넣지 않는다)
  3 ~/.config/ur-harness/harness.yaml|json
  4 기존 ~/.nworks/config.json    이관 호환 — 있으면 매핑해 가져온다
  5 환경변수 URH_<대문자_경로>     예: URH_ORG__DOMAIN=example.org

쓰는 법
    from lib.config import cfg, require, paths
    cfg("org.drives.ssot.remote_ro", "SSOT_ro")
    require("operator.account")        # 미설정이면 거기서 멈춘다
    paths.workspace                     # 확장된 Path
"""
from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path
from typing import Any

_PLACEHOLDER = re.compile(r"^<.*>$|^\{\{.*\}\}$")
_REPO = Path(__file__).resolve().parents[2]          # ur-harness/
_CACHE: dict | None = None


def _read(path: Path) -> dict:
    if not path.is_file():
        return {}
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return {}
    if path.suffix in (".yaml", ".yml"):
        try:
            import yaml                                # noqa: PLC0415
        except ImportError:
            print(f"! pyyaml 이 없어 {path.name} 을 건너뛴다 — "
                  f"pip install pyyaml 또는 같은 이름의 .json 을 쓴다", file=sys.stderr)
            return {}
        try:
            return yaml.safe_load(text) or {}
        except Exception as e:                         # noqa: BLE001
            print(f"! {path.name} 파싱 실패: {e}", file=sys.stderr)
            return {}
    try:
        return json.loads(text)
    except Exception as e:                             # noqa: BLE001
        print(f"! {path.name} 파싱 실패: {e}", file=sys.stderr)
        return {}


def _first(base: Path, stem: str) -> dict:
    for ext in (".yaml", ".yml", ".json"):
        d = _read(base / f"{stem}{ext}")
        if d:
            return d
    return {}


def _merge(a: dict, b: dict) -> dict:
    """b 가 a 를 덮는다. 자리표시자는 덮지 못한다 — 실제 값을 지우지 않기 위해서."""
    out = dict(a)
    for k, v in (b or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _merge(out[k], v)
        elif isinstance(v, str) and _PLACEHOLDER.match(v.strip()) and k in out:
            continue
        else:
            out[k] = v
    return out


def _from_legacy(d: dict) -> dict:
    """기존 그룹웨어 에이전트 설정(~/.nworks/config.json)을 새 스키마로 옮긴다.

    이관할 때 기존 설정을 버리지 않아도 되도록 하는 호환 계층이다.
    비밀값(client_secret·bot_secret·private_key_path)은 **여기서 읽지 않는다** —
    그것은 인증 모듈이 원본 파일에서 직접 읽는다.
    """
    if not d:
        return {}
    out: dict[str, Any] = {}
    if d.get("bot_id"):
        out.setdefault("org", {}).setdefault("accounts", {})["bot_id"] = d["bot_id"]
    accts = d.get("mail_accounts") or []
    if accts:
        out.setdefault("org", {}).setdefault("accounts", {})["bot"] = accts[0]
    ch: dict[str, Any] = {}
    if d.get("dept_channel_id"):
        ch["dept"] = d["dept_channel_id"]
    if d.get("archive_channels"):
        ch["archive"] = list(d["archive_channels"])
    if ch:
        out["channels"] = ch
    return out


def _from_env() -> dict:
    """URH_ORG__DOMAIN=example.org → {"org": {"domain": "example.org"}}"""
    out: dict[str, Any] = {}
    for key, val in os.environ.items():
        if not key.startswith("URH_"):
            continue
        parts = [p.lower() for p in key[4:].split("__") if p]
        if not parts:
            continue
        node = out
        for p in parts[:-1]:
            node = node.setdefault(p, {})
        node[parts[-1]] = val
    return out


def load(reload: bool = False) -> dict:
    global _CACHE
    if _CACHE is not None and not reload:
        return _CACHE
    merged = _first(_REPO / "config", "harness.example")
    merged = _merge(merged, _first(_REPO / "config", "harness"))
    merged = _merge(merged, _first(Path.home() / ".config/ur-harness", "harness"))
    merged = _merge(merged, _from_legacy(_read(Path.home() / ".nworks/config.json")))
    merged = _merge(merged, _from_env())
    _CACHE = merged
    return merged


def cfg(dotted: str, default: Any = None) -> Any:
    """`cfg("org.drives.ssot.id")`. 자리표시자는 미설정으로 본다."""
    node: Any = load()
    for part in dotted.split("."):
        if not isinstance(node, dict) or part not in node:
            return default
        node = node[part]
    if isinstance(node, str) and (_PLACEHOLDER.match(node.strip()) or not node.strip()):
        return default
    return node


def require(dotted: str) -> Any:
    """없으면 멈춘다. 설정이 안 된 채로 도는 것보다 멈추는 쪽이 낫다."""
    v = cfg(dotted)
    if v is None:
        raise SystemExit(
            f"설정이 없다: {dotted}\n"
            f"  config/harness.yaml 에 값을 넣는다 "
            f"(config/harness.example.yaml 을 복사해 시작한다)")
    return v


def missing() -> list[str]:
    """아직 자리표시자인 키를 모아 돌려준다 — 설치 점검(doctor)이 쓴다."""
    out: list[str] = []

    def walk(node: Any, prefix: str = "") -> None:
        if isinstance(node, dict):
            for k, v in node.items():
                walk(v, f"{prefix}.{k}" if prefix else k)
        elif isinstance(node, str) and _PLACEHOLDER.match(node.strip()):
            out.append(prefix)

    walk(load())
    return sorted(out)


class _Paths:
    """확장된 경로. 설정에 없으면 합리적인 기본값을 쓴다."""

    @property
    def repo(self) -> Path:
        return _REPO

    @property
    def workspace(self) -> Path:
        p = Path(cfg("operator.workspace", "~/ur-harness-work")).expanduser()
        return p

    @property
    def state(self) -> Path:
        """Heartbeats, cursors, boards.

        `state.root` wins when set: the operating tree may keep state outside
        the workspace, and launchd passes the same value as UR_STATE. Deriving
        it from the workspace instead silently writes to a second directory
        that nothing reads.
        """
        if (v := os.environ.get("UR_STATE")):
            return Path(v)
        if (v := cfg("state.root")):
            return Path(v).expanduser()
        return self.workspace / "state"

    @property
    def logs(self) -> Path:
        return self.workspace / "logs"

    @property
    def output(self) -> Path:
        return self.workspace / "output"

    @property
    def stage(self) -> Path:
        """파싱·정제된 중간 산출물. 승격 전 단계다."""
        return self.workspace / "stage"

    def raw(self, kind: str = "") -> Path:
        """수집 원본 (mail · messenger · calendar). 불변 로그로 다룬다.

        두 가지 배치를 모두 받는다 — `raw/<종류>` 와 `raw_<종류>`.
        운영 트리가 후자이기 때문이다. **있는 쪽을 쓴다.**
        없는 쪽을 가리키면 빈 폴더를 읽고 「0건」을 사실로 보고하게 된다.
        """
        if not kind:
            return self.workspace / "raw"
        nested = self.workspace / "raw" / kind
        flat = self.workspace / f"raw_{kind}"
        if nested.is_dir():
            return nested
        if flat.is_dir():
            return flat
        return nested                       # 둘 다 없으면 표준 배치를 돌려준다

    @property
    def ledger(self) -> Path:
        """태스크 원장 (jsonl)."""
        return self.workspace / "tasks" / "ledger.jsonl"

    @property
    def weekly(self) -> Path:
        """주간보고 수집물(parsed · attachments). 작업공간 안에 둔다 —
        예전에는 형제 프로젝트 폴더를 가리켰는데, 그 폴더는 저장소에 없어서
        새로 설치한 사람에게는 처음부터 빈 경로였다."""
        return self.workspace / "weekly"

    @property
    def service_account(self) -> Path:
        """Drive API 자격. 설정이 소유한다.

        예전에는 작업공간 폴더 이름으로 조립했다(`sa_{slug}.json`). 그 이름이
        `output` 이어서 `sa_output.json` 을 찾았고, 키는 있는데 「키 없음」이
        보고됐다 (2026-10-22 실측).
        """
        v = cfg("api.service_account")
        return Path(v).expanduser() if v else Path.home() / ".rpb/sa.json"

    @property
    def mount_root(self) -> Path | None:
        v = cfg("operator.mount_root")
        return Path(v).expanduser() if v else None

    def ssot(self, *parts: str) -> str:
        """SSOT 안의 상대 경로. 설정된 부서 폴더를 앞에 붙인다."""
        return "/".join(str(p).strip("/") for p in parts if p)


paths = _Paths()


if __name__ == "__main__":
    miss = missing()
    print(f"설정 키 {len(load())}개 블록 · 미설정 {len(miss)}개")
    for m in miss:
        print(f"  · {m}")
    print(f"\nworkspace: {paths.workspace}")
    print(f"mount_root: {paths.mount_root or '(쓰지 않음)'}")
