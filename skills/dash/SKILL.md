---
name: dash
description: 부서 대시보드를 고치고 다시 만든다. 정부과제 시스템 매뉴얼(e나라도움·NIPA·질병청·K-Startup·RCMS 등)처럼 JSON 소스에서 HTML 화면을 생성하는 대시보드를 편집·빌드·배포할 때 쓴다. "대시보드 고치기", "매뉴얼 보완", "e나라도움 페이지 수정", /dash 로 호출.
---

# dash — 대시보드를 고치고 다시 만든다

## 이 스킬이 지키는 한 가지

> **JSON 이 정본이고 HTML 은 생성물이다.**
> HTML 을 직접 고치면 다음 빌드에 사라진다. **반드시 JSON 을 고치고 빌드한다.**

2026-08~09 에 이 원칙을 몰라 HTML 을 직접 고쳤고, 세 페이지(nipa·kdca·kstartup)가
빌드 두 번에 유실됐다. 이 스킬의 존재 이유가 그것이다.

---

## 0단계. 환경 확인 (묻지 않고 확인한다)

```bash
python3 -V || python -V || py -V          # 셋 중 되는 것
```

**파이썬이 없으면 여기서 멈추고 설치를 안내한다.** 빌드에 반드시 필요하다.

| OS | 설치 |
|---|---|
| Windows | Microsoft Store 에서 「Python 3」 설치, 또는 python.org/downloads (설치 시 **Add to PATH** 체크) |
| macOS | 이미 있다 (`python3`) |

설치 후 터미널을 다시 열어야 인식된다.

소스 위치 (SSOT 안에 있다. 각자 PC 에 저장소를 두지 않는다):

```
<SSOT>/50_도구상자/20_소스/gov-system-manuals/
├─ data/            ← 고치는 곳
└─ scripts/build.py ← 실행하는 것
```

`<SSOT>` 는 `works_env.py` 로 찾는다(같은 폴더에 있다). macOS 는
`~/Library/CloudStorage/GoogleDrive-*/공유 드라이브/{{SSOT_DRIVE}}`,
Windows 는 `G:\공유 드라이브\{{SSOT_DRIVE}}` 형태다.

---

## 1단계. 무엇을 고칠지 정한다

| 고치려는 것 | 고칠 파일 |
|---|---|
| 특정 시스템의 절차·화면 설명 | `data/procedures/<code>.json` |
| 시스템 목록·과제 상태·탭 구성 | `data/systems.json` |
| 계좌·카드 | `data/accounts.json` |
| 참여인력·인건비 | `data/people.json` |
| 다운로드 자료 | `data/downloads.json` |

`<code>` 는 `systems.json` 의 시스템 코드다 (`enara` · `nipa` · `kdca` · `kstartup` · `pms` · `rcms` · `iris` · `g2b`).

> **`procedures/<code>.json` 이 없으면 그 페이지는 빈 껍데기로 생성된다.**
> 내용을 채우려면 그 파일을 만들어야 한다. `pms.json` 이 형식 참고용이다.

---

## 2단계. 소유권 확인 (충돌 방지)

여러 사람이 같은 소스를 쓴다. **규칙이 필요한 파일은 하나뿐이다.**

| 파일 | 소유 | 규칙 |
|---|---|---|
| `procedures/<code>.json` | 그 시스템 담당자 | 사실상 1인 1파일. 조율 불필요 |
| `accounts.json` · `people.json` · `downloads.json` | 해당 업무 담당자 | 낮은 충돌. 고치기 전 최근 수정일만 확인 |
| **`systems.json`** | **공용** | **여기만 조율한다.** 고치기 전 `01_협업` 에 한 줄 남기거나 담당자에게 알린다 |

**생성물(HTML)은 충돌 개념이 없다.** 누가 덮어써도 다시 빌드하면 된다.
HTML 을 지키려 애쓰지 않는다 — 지킬 것은 JSON 이다.

---

## 3단계. 고친다

JSON 을 편집한다. 편집 전 원본을 기억해 두고, 구조를 깨지 않는다.

- 문자열 안의 따옴표·줄바꿈은 JSON 규칙을 따른다
- **모르는 것은 추측으로 채우지 않는다.** 「기록 없음」으로 남긴다
- 근거를 함께 적는다 (발신자·날짜 또는 매뉴얼명)

편집 후 문법을 확인한다.

```bash
python3 -c "import json,sys; json.load(open(sys.argv[1],encoding='utf-8')); print('JSON OK')" <파일>
```

---

## 4단계. 빌드한다

```bash
python3 <SSOT>/50_도구상자/20_소스/gov-system-manuals/scripts/build.py \
        --data <SSOT>/50_도구상자/20_소스/gov-system-manuals/data \
        --out  <SSOT>/{{DEPT_ROOT}}/50_대시보드/20_정부과제시스템
```

Windows PowerShell 은 줄바꿈 대신 백틱(`` ` ``)을 쓰거나 한 줄로 적는다.

**`--out` 을 대시보드 폴더로 직접 지정하면 빌드가 곧 배포다.** 별도 업로드 단계가 없다.

빌드 후 확인할 것.

| 확인 | 왜 |
|---|---|
| 고친 페이지의 **파일 크기가 줄지 않았는지** | 소스가 비면 빈 껍데기가 생성된다. 크게 줄었으면 JSON 을 다시 본다 |
| 브라우저로 열어 화면 확인 | 로컬 마운트라 더블클릭하면 그대로 열린다 |

---

## 5단계. 남긴다

- 무엇을 왜 고쳤는지 **JSON 안에 근거로** 남긴다 (`_출처` · `근거` 필드)
- 큰 변경이면 `01_협업` 에 한 줄 알린다

---

## 하지 않는 것

- **HTML 직접 편집** — 다음 빌드에 사라진다. 예외 없다
- **소스를 자기 PC 로 복사해 고치기** — 각자의 사본이 갈라진다. SSOT 의 것을 직접 고친다
- `data/` 밖의 파일 수정 — `scripts/build.py` 는 도구다. 화면을 바꾸려면 JSON 을 고친다

---

## 참고

| 무엇 | 어디 |
|---|---|
| 화면 구조·CSS 클래스 규약 | `{{DEPT_ROOT}}/50_대시보드/20_정부과제시스템/_인수인계_클로드코드_20260910.md` §4 |
| 원본 자료 위치 (공식 매뉴얼) | 같은 문서 §5 |
| 유실 경위 | 같은 폴더 `정부과제시스템 인수인계.md` |
| 작업 규약 | `50_도구상자/00_규범/` |
