import json, re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.request import Request, urlopen
from html import unescape

JST=timezone(timedelta(hours=9))
BASE="https://www.jra.go.jp"
OUT=Path("data/races.json")

def get(url):
    req=Request(url,headers={"User-Agent":"Mozilla/5.0 (compatible; KeibaSite/1.0)"})
    with urlopen(req,timeout=20) as r:
        return r.read().decode("utf-8","ignore")

def clean(s):
    return re.sub(r"\s+"," ",unescape(re.sub("<[^>]+>"," ",s))).strip()

def extract_race_links(html):
    # JRA race pages use /JRADB/accessD.html?CNAME=... .
    links=re.findall(r'href=["\']([^"\']*JRADB/accessD\.html\?CNAME=[^"\']+)["\']',html)
    out=[]
    for x in links:
        if x.startswith("/"): x=BASE+x
        elif x.startswith("http"): pass
        else: x=BASE+"/"+x
        if x not in out: out.append(x)
    return out

def parse_race(url):
    html=get(url)
    text=clean(html)
    m=re.search(r"(\d{4}年\d{1,2}月\d{1,2}日).{0,120}?(\d{1,2})レース",text)
    date=m.group(1) if m else ""
    race_no=int(m.group(2)) if m else None
    title=""
    tm=re.search(r"発走時刻：\d{1,2}時\d{2}分.{0,120}?<[^>]*>\s*([^<]{2,80})",html)
    if tm: title=clean(tm.group(1))
    if not title:
        tm=re.search(r"レース.{0,50}?(\d{1,2}レース)",text)
    venue=""
    vm=re.search(r"(\d+回)?([^\s]{2,6})(\d+)日",text)
    if vm: venue=vm.group(2)

    cm=re.search(r"コース：\s*([\d,]+)メートル（(芝|ダート)[^）]*）",text)
    distance=cm.group(1).replace(",","")+"m" if cm else ""
    surface=cm.group(2) if cm else ""

    # Pull rows from the visible race table. This intentionally keeps parsing conservative.
    horses=[]
    row_pat=re.compile(r'>(\d{1,2})<.*?>([^<]{1,30})',re.S)
    for mm in row_pat.finditer(html):
        no=int(mm.group(1)); name=clean(mm.group(2))
        if name and not any(h["horse_no"]==no for h in horses):
            if not re.search(r"^(枠|馬番|馬名|人気)$",name):
                horses.append({"horse_no":no,"horse":name})
    horses=horses[:18]

    return {
      "venue":venue or "JRA",
      "race_no":race_no or 0,
      "name":title or f"{race_no or ''}R",
      "distance":distance,
      "surface":surface,
      "track_condition":"未取得",
      "predictions":[],
      "horses":horses,
      "source_url":url,
      "commentary":"予測エンジン投入前の出馬表データです。"
    }

def main():
    now=datetime.now(JST)
    # JRA's current race information page contains links to the active cards.
    candidates=[]
    for path in ["/JRADB/accessD.html","/JRA-Info/"]:
        try:
            candidates += extract_race_links(get(BASE+path))
        except Exception:
            pass
    # Keep only a manageable set and deduplicate.
    races=[]
    for url in candidates[:80]:
        try:
            r=parse_race(url)
            if r["race_no"]:
                races.append(r)
        except Exception as e:
            print("skip",url,e)
    # Same race can appear multiple times in navigation; dedupe by URL.
    seen=set(); unique=[]
    for r in races:
        if r["source_url"] not in seen:
            seen.add(r["source_url"]); unique.append(r)
    payload={"updated_at":now.isoformat(),"source":"JRA","races":unique[:36]}
    OUT.write_text(json.dumps(payload,ensure_ascii=False,indent=2),encoding="utf-8")
    print(f"wrote {len(unique[:36])} races")

if __name__=="__main__":
    main()
