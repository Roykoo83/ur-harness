#!/usr/bin/env python3
"""archify 산출 HTML 의 폰트 폴백에 한글 폰트를 끼워 넣는다.

archify 템플릿의 폴백은 중국어·일본어 폰트뿐이라(PingFang SC, Hiragino Sans GB,
Microsoft YaHei) 한글 글리프가 없다. 구글 폰트를 못 받는 환경에서 한글이 두부로 나온다.
렌더러는 건드리지 않고 산출물만 고친다. 다시 렌더링하면 이 스크립트를 또 돌려야 한다.

  python3 _한글폰트주입.py 회의록흐름.html 전사구조.html
"""
import io
import sys

KO = ("'Apple SD Gothic Neo','Pretendard','Noto Sans KR',"
      "'Malgun Gothic','NanumGothic',")
ANCHOR = "'Noto Sans Mono CJK SC'"

for path in sys.argv[1:]:
    s = io.open(path, encoding="utf-8").read()
    if "Apple SD Gothic Neo" in s:
        print(f"[skip] {path} 이미 적용됨")
        continue
    n = s.count(ANCHOR)
    if not n:
        print(f"[!!] {path} 폴백 위치를 못 찾음")
        continue
    s = s.replace(ANCHOR, KO + ANCHOR)
    io.open(path, "w", encoding="utf-8").write(s)
    print(f"[ok] {path} 폴백 {n}곳에 한글 폰트 주입")
