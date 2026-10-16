# 스킬 — OS 별 설치와 사용

## 불변식

> **도구가 없으면 느려질 뿐, 못 하게 되지 않는다.**

모든 스킬은 `requires` 가 **비어 있다.** 프로그램이 깔려 있으면 자동화되고,
없으면 AI 가 직접 해서 같은 결과를 낸다. **설치를 조건으로 걸지 않는다.**

그리고 느린 길로 갈 때는 **무엇이 빠졌는지 말한다.** 조용히 물러나지 않는다.

## 능력 선언 — 모든 SKILL.md 가 갖는다

```yaml
portability:
  tier: T0
  requires: []                                     # 없으면 성립 불가. 비어 있는 것이 정상
  enhanced_by: ["python3: 브리핑 자동 생성"]         # 있으면 빨라진다
  degraded:    ["python3 없음 → AI 가 직접 읽는다"]   # 없을 때 대신 하는 일
  verified_on: ["실측한 환경만 적는다"]
  gaps:        ["알면서 못 메운 것"]
```

등급은 선언에서 나온다.

| 등급 | 뜻 |
|---|---|
| **T0** | 도구 없이도 결과가 나온다. **모든 스킬의 최소 기준** |
| T1 | 셸이 있으면 빨라진다 |
| T2 | python·node 가 있으면 자동화된다 |
| T3 | 전용 도구가 필요한 단계가 있다 |

같은 스킬이 환경에 따라 T0 으로도 T3 으로도 돈다. **그것이 이식의 정의다.**

## 설치

스킬은 **파일을 두는 것이 전부**다. 클라이언트가 그 폴더를 읽는다.

### Linux · macOS

```bash
mkdir -p ~/.claude/skills
cp -R skills/* ~/.claude/skills/
```

### Windows (PowerShell)

```powershell
New-Item -ItemType Directory -Force "$HOME\.claude\skills" | Out-Null
Copy-Item -Recurse -Force .\skills\* "$HOME\.claude\skills\"
```

### 클라이언트가 스킬을 자동 인식하지 않을 때

그 `SKILL.md` 를 열어 절차를 그대로 따르게 한다. 스킬은 **실행 파일이 아니라 절차서**이므로
자동 인식이 없어도 작동한다.

## 스킬별 실행 요구

| 스킬 | 하는 일 | 있으면 좋은 것 | 없을 때 |
|---|---|---|---|
| **과제 작업 시작** | 복귀 브리핑 · 작업 세팅 | python3 | AI 가 폴더를 직접 읽어 브리핑을 쓴다 (느림, 결과 동일) |
| **회의록** | 전사 → 교정 → 확정본 | python3 · ffmpeg | 전사를 사람이 붙여넣고 AI 가 교정한다 |
| **구성도** | 다이어그램 HTML | **node** | 현재 T0 경로 없음 — `gaps` 참조 |
| **대시보드** | 현황판 생성·수정 | python3 | AI 가 HTML 을 직접 쓴다 (1종 수정 가능) |
| **사내 수집** | 로그인 필요 페이지 | 브라우저 자동화 | 사람이 수집하고 AI 가 정리한다 |

## OS 별로 확인할 것

| | Linux | macOS | Windows |
|---|---|---|---|
| python3 | `python3` | `python3` | `py` 또는 `python` |
| 셸 | bash/zsh | zsh | PowerShell |
| 경로 구분 | `/` | `/` | `\` (스킬이 알아서 처리) |
| 드라이브 | 마운트 지점 | `~/Library/CloudStorage/…` | 드라이브 문자 (`G:`) |

**스킬은 환경을 묻지 않고 직접 확인한다.** 사람에게 OS 를 묻지 않는다.

## 새 스킬을 추가할 때

1. `skills/<이름>/SKILL.md` — frontmatter 에 `name` · `description` · `portability`
2. `requires` 가 비어 있지 않으면 **스킬이 아니다** — 그것은 관리자 도구이므로 `core/` 나 `agents/` 로
3. `verified_on` 에는 **실측한 환경만** 적는다. 추정을 적지 않는다
4. 못 메운 것은 `gaps` 에 남긴다. 숨기지 않는다
