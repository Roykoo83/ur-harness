#!/usr/bin/env python3
"""1차 수집에서 나온 링크를 실제 page id 로 해석한다.

전사 주간보고는 본문에 부서별 '링크 표'만 두고 내용은 다른 페이지에 있다.
그 페이지들은 자식이 아니므로 계층 순회로는 절대 잡히지 않는다.

- /wiki/x/<key>  단축링크 → 리다이렉트를 따라가 page id 추출
- [제목] 링크     → CQL title 검색으로 id 확보

사용: confluence-resolve-links.py <surface> <링크목록.json> <출력.json>
"""
import json
import subprocess
import sys
import time
from pathlib import Path


def ev(surface, js, timeout=90):
    r = subprocess.run(["cmux", "browser", "--surface", surface, "eval", js],
                       capture_output=True, timeout=timeout)
    return r.stdout.decode("utf-8", "replace").strip()


def jev(surface, js, timeout=90):
    raw = ev(surface, js, timeout)
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return None


JS_SHORT = """(async()=>{
try{const r=await fetch('/wiki/x/%s',{redirect:'follow'});
const u=r.url||'';const m=u.match(/pages\\/(\\d+)/);
return JSON.stringify({key:'%s',url:u,id:m?m[1]:null});}
catch(e){return JSON.stringify({key:'%s',error:String(e)});}})()"""

JS_TITLE = """(async()=>{
try{const q=encodeURIComponent('title = "%s"');
const r=await fetch('/wiki/rest/api/search?cql='+q+'&limit=3',{headers:{Accept:'application/json'}});
const d=await r.json();
const hit=(d.results||[]).find(x=>x.content);
return JSON.stringify({title:"%s",id:hit?hit.content.id:null});}
catch(e){return JSON.stringify({title:"%s",error:String(e)});}})()"""


def main():
    surface, src, dst = sys.argv[1], Path(sys.argv[2]), Path(sys.argv[3])
    data = json.loads(src.read_text(encoding="utf-8"))
    found, missed = {}, []

    shorts = data.get("short", [])
    print(f"단축링크 {len(shorts)}개 해석 중...", flush=True)
    for i, k in enumerate(shorts, 1):
        r = jev(surface, JS_SHORT % (k, k, k))
        if r and r.get("id"):
            found[r["id"]] = f"/wiki/x/{k}"
        else:
            missed.append({"short": k, "result": r})
        if i % 20 == 0:
            print(f"  {i}/{len(shorts)}", flush=True)
        time.sleep(0.1)

    titles = data.get("titles", [])
    print(f"제목링크 {len(titles)}개 해석 중...", flush=True)
    for i, t in enumerate(titles, 1):
        esc = t.replace('"', '\\"')
        r = jev(surface, JS_TITLE % (esc, esc, esc))
        if r and r.get("id"):
            found[r["id"]] = t
        else:
            missed.append({"title": t, "result": r})
        if i % 25 == 0:
            print(f"  {i}/{len(titles)}", flush=True)
        time.sleep(0.1)

    for pid in data.get("ids", []):
        found.setdefault(pid, "direct")

    dst.write_text(json.dumps({
        "resolved": found, "missed": missed,
        "counts": {"resolved": len(found), "missed": len(missed)},
    }, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"\n해석 성공 {len(found)}개 · 실패 {len(missed)}개 → {dst}")


if __name__ == "__main__":
    main()
