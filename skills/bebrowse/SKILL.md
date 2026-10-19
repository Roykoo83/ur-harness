---
name: bebrowse
description: cmux 실브라우저(사용자 로그인 세션 그대로)를 표준 에이전트 루프로 조작하는 절차 — 사이트 조사, 사내 시스템(Confluence 등) 수집, 로그인 필요한 페이지 자동화, 웹 반복 작업에 사용. browser-use 패턴을 자체 스택으로 구현한 것.
---

# bebrowse (BE:browse) — cmux 실브라우저 에이전트 루프

핵심 원리: **인식은 snapshot, 행동은 표준 액션, 추출은 eval, 대량은 Python.**
사용자의 실제 브라우저를 쓰므로 로그인·MFA·내부망 접근이 이미 되어 있다 — 이것이
별도 브라우저를 띄우는 프레임워크(browser-use·Playwright) 대비 이 방식의 최대 이점이다.

## 표준 루프

```bash
# 0. 패널 확보 — 기존 브라우저 패널 재사용 우선, 없으면 열기
cmux list-panels                                  # browser surface 확인
S=$(cmux browser open <url> --focus false | grep -oE 'surface:[0-9]+')

# 1. 대기 — sleep 금지, 조건 대기만
cmux browser $S wait --load-state complete --timeout 15
cmux browser $S wait --selector "css" / --text "문구" / --url-contains "path"

# 2. 인식 — 스크린샷보다 먼저 스냅샷 (인터랙티브 요소가 [ref]와 함께 나옴)
cmux browser $S snapshot --interactive --compact
cmux browser $S screenshot --out /절대/경로.png    # 시각 확인이 필요할 때만

# 3. 행동 — 표준 액션 + 매 행동 뒤 결과 확인
cmux browser $S click "css" --snapshot-after
cmux browser $S fill "css" --text "값"
cmux browser $S press Enter / select "css" --value v / scroll --dy 800
cmux browser $S find role button --name "저장"      # 셀렉터 대신 role/text/label 탐색

# 4. 추출 — 단건은 get, 구조화는 eval(JSON 반환)
cmux browser $S get text --selector "css" / get title / get url
cmux browser $S eval 'JSON.stringify([...document.querySelectorAll("a")].map(a=>a.href))'
```

## 검증된 규칙 (2026-07~08 실전 축적)

1. **로그인은 사람이 한다.** 자격증명 입력·저장 금지. 로그인이 필요하면 사용자에게 패널을 넘긴다.
2. **대량 수집은 사이트 API 우선** — 브라우저 `fetch`는 세션 인증을 그대로 태운다
   (Confluence v2 API 수집으로 검증). 페이지 순회·재귀는 JS가 아닌 **Python에서 조립**
   (JS 쪽 재귀는 eval 타임아웃), 첨부 8MB 초과는 다운로드하지 말고 경로만 기록.
3. 셸 스크립트에서 cmux 호출 시 `PATH=/usr/bin:/opt/homebrew/bin:$PATH` 명시 (패인 PATH 결핍).
4. 출력 파일은 **절대경로만** (상대경로 → 엉뚱한 중첩 폴더 생성).
5. **연속 실패 3회 → 패널 생존 확인 후 중단** (패널 닫힘 미감지로 44연속 실패 사례).
6. 완료 판정은 파일·명령 결과로 한다 — read-screen·스크롤백에는 이전 출력이 남아 오탐한다.
7. 대량 반복 발주 전 건수를 프로젝트 logs/에 남기고, 수집 후 건수·산출물 경로를 기록한다.
8. 끝나면 테스트로 연 패널은 닫는다: `cmux close-surface --surface $S`

## 실전 패턴 (2026-08-28 네이버웍스 캘린더 일정 생성에서 검증)

- **마커 + 네이티브 클릭**: JS `el.click()`이 안 먹는 React/커스텀 위젯은
  eval로 대상에 `el.setAttribute("data-cx","t1")` 마커를 붙이고
  `cmux browser $S click '[data-cx="t1"]'`로 클릭한다. 텍스트로 찾은 요소를
  CSS 셀렉터 액션으로 넘기는 범용 브리지.
- **드롭다운은 겉이 아니라 속**: `.select_cover` 류 커스텀 셀렉트는 겉 div가 아니라
  내부 `button`에 핸들러가 있다. 안 열리면 `outerHTML.slice(0,600)`으로 실구조 확인 후 조준.
- **datepicker는 타이핑이 안정적**: 달력 셀 클릭보다 입력창 `fill` + `press Enter`가 잘 먹는다.
- **쓰기 작업 2중 검증**: 저장 전 모든 필드값을 eval로 확인하고, 저장 후 목적 화면에서
  결과 존재를 재확인한다 (읽기와 달리 쓰기는 검증 생략 금지).

## 환경 이식 — cmux가 없는 팀원 (2026-08-28 Playwright로 실측 검증)

이 스킬의 루프·규칙은 엔진 중립이다. cmux 명령만 환경별 등가물로 치환하면 된다:

| bebrowse (cmux) | Playwright / playwright-mcp 등가물 |
|---|---|
| `snapshot --interactive` | `aria_snapshot()` / MCP `browser_snapshot` |
| `find role button --name "저장"` | `get_by_role("button", name="저장")` |
| `click` · `fill` · `press` · `select` | 동명 메서드 그대로 |
| `wait --selector/--load-state` | `wait_for_selector` / `wait_for_load_state` |

환경별 레일: **VSCode·Claude Code CLI·Codex** → playwright-mcp(MS 공식) 또는 Python
playwright 스크립트 · **Claude 데스크탑** → playwright-mcp 또는 Claude for Chrome(실브라우저
=실세션) · **Antigravity** → 자체 브라우저 에이전트 내장(Chrome 연동, 실세션).

**무설치 레일 (macOS 공통, 2026-08-28 실측)**: 셸만 있으면 OS 내장 `osascript`로 사용자의
실제 Chrome을 조작한다 — 프레임워크 0, 실세션 그대로. 탭 열기·URL/제목 읽기·탭 관리는
즉시 되고(자동화 TCC 승인만), **JS 실행(eval 등가)은 Chrome 메뉴 보기 > 개발자 >
"Apple Events의 자바스크립트 허용" 1회 토글** 후 가능:
`osascript -e 'tell app "Google Chrome" to execute front window'\''s active tab javascript "..."'`
주의: 이 레일은 JS-only라 네이티브 클릭 폴백이 없다 — 신뢰 이벤트를 요구하는 위젯은
"타이핑+Enter" 패턴(위 실전 패턴 참조)과 System Events 키 입력으로 우회한다.

로그인 세션: cmux·Chrome확장·Antigravity는 실브라우저라 이미 로그인됨. playwright 계열은
`launch_persistent_context(user_data_dir=전용 프로필)`로 **최초 1회만 사람이 로그인**하면
이후 세션이 유지된다. 어떤 환경에서든 자격증명 입력·저장 금지 규칙은 동일.

## 전체 페이지 보관

스크린샷+텍스트+링크 일괄 캡처: `scripts/browser-scan.sh` (AX_Infra·{{WORKSPACE_NAME}} 양쪽 존재).

## 한계와 대안

- 대량 병렬·봇차단 우회(프록시·캡차)가 필요한 **외부** 크롤링 → Playwright 스크립트 또는
  browser-use Cloud 검토. 사내·로그인 사이트는 이 스킬로 충분.
- 반복이 정형화되면 LLM 루프를 떼고 **Python/셸 스크립트로 고착**시킨다 (토큰 0원, 재현 100%).
- **headless 워커 발주 검증됨 (2026-08-28)**: 보조 워커 모두 정밀 레시피(명령 그대로 + 단계별
  기대값 + 출력 파일 지정)를 주면 캘린더 일정 생성 같은 쓰기 작업까지 완주한다. 운영 규칙:
  - 워커마다 **자기 surface를 새로 열게** 한다 (동시 발주 충돌 방지, 보조 워커 동시 실행 검증됨)
  - 발주 전 `cmux browser open about:blank` **스모크 테스트** — 실패 원인 1순위는 워커 쿼터,
    2순위는 cmux 웹뷰 상태(고장 시 open이 IPC 큐 전체를 막음, 앱 재시작으로 해소)
  - **내비게이션을 유발하는 클릭(저장·제출)은 JS 응답이 유실될 수 있다** — 타임아웃이라고
    바로 재클릭하지 말고 `get url`로 화면 전환 여부부터 확인 (재클릭은 중복 제출 위험).
    워커 지시문에도 이 규칙을 넣을 것.
