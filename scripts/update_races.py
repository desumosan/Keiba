import json, re, html
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.request import Request, urlopen
from urllib.parse import urljoin

JST=timezone(timedelta(hours=9))
BASE="https://www.jra.go.jp"
OUT=Path("data/races.json")

def get(url):
    req=Request(url,headers={
        "User-Agent":"Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/140 Safari/537.36",
        "Accept-Language":"ja-JP,ja;q=0.9"
    })
    with urlopen(req,timeout=30) as r:
        return r.read().decode("utf-8","ignore")

def clean(s):
    s=html.unescape(s)
    s=re.sub(r"<[^>]+>"," ",s)
    return re.sub(r"\s+"," ",s).strip()

def race_links(page):
    # Do not assume a particular HTML quoting/relative-link format.
    page=html.unescape(page)
    found=re.findall(r'(?:href=["\']?[^"\'>\s]*?accessD\.html\?CNAME=|(?:https?:)?//www\.jra\.go\.jp/JRADB/accessD\.html\?CNAME=)([^"\'<>\s&]+)',page)
    urls=[]
    for cname in found:
        url=f"{BASE}/JRADB/accessD.html?CNAME={cname}"
        if url not in urls and "2026" in cname:
            urls.append(url)
    return urls

def parse_race(url):
    raw=get(url)
    text=clean(raw)

    m=re.search(r"(\d{4}年\d{1,2}月\d{1,2}日).*?(\d{1,2})レース",text)
    if not m:
        return None
    race_no=int(m.group(2))

    # The page exposes the meeting, race title, course and weather as text.
    vm=re.search(r"\d{4}年\d{1,2}月\d{1,2}日.*?(\d+回[^\s]+?\d+日)",text)
    venue=vm.group(1) if vm else "JRA"

    # Extract title between start time and class/course information.
    title=""
    tm=re.search(r"発走時刻：\d{1,2}時\d{2}分\s*(.*?)\s*(?:\d+歳|2歳|3歳|4歳以上|サラ系)",text)
    if tm:
        title=tm.group(1).strip()
    if not title:
        tm=re.search(r"発走時刻：\d{1,2}時\d{2}分\s*(.{2,60}?)(?:コース：)",text)
        if tm: title=tm.group(1).strip()

    cm=re.search(r"コース：\s*([\d,]+)メートル（(芝|ダート)[^）]*）",text)
    distance=cm.group(1).replace(",","")+"m" if cm else ""
    surface=cm.group(2) if cm else ""

    weather=re.search(r"天候([晴曇雨雪]+)",text)
    track=re.search(r"(芝|ダート)(良|稍重|重|不良)",text)
    condition=track.group(2) if track else "未取得"

    horses=[]
    # Conservative extraction from table text. The pattern captures horse number + name + odds.
    # It intentionally accepts names containing Japanese punctuation.
    pat=re.compile(r"(?:枠\d+\w*\s*)?(\d{1,2})\s+([^\d\s]{1,20})\s+([0-9]+\.[0-9]+)\((\d+)番人気\)")
    for mm in pat.finditer(text):
        no=int(mm.group(1)); name=mm.group(2)
        if not any(x["horse_no"]==no for x in horses):
            horses.append({
                "horse_no":no,
                "horse":name,
                "odds":float(mm.group(3)),
                "popularity":int(mm.group(4))
            })

    # Fallback for new/debut races where the odds are not always present.
    if not horses:
        pat2=re.compile(r"(?:枠\d+\w*\s*)?(\d{1,2})\s+([^\d\s]{2,20})\s+(?=\d+\.\d+\()")
        for mm in pat2.finditer(text):
            no=int(mm.group(1)); name=mm.group(2)
            if not any(x["horse_no"]==no for x in horses):
                horses.append({"horse_no":no,"horse":name})

    return {
        "venue":venue,
        "race_no":race_no,
        "name":title or f"{race_no}R",
        "distance":distance,
        "surface":surface,
        "track_condition":condition,
        "horses":horses,
        "predictions":[],
        "commentary":"出馬表取得済み。予測エンジンは次の段階で追加します。",
        "source_url":url
    }

def main():
    urls=[]
    # These pages are JRA's public race-data entry points. Search broadly for CNAME links.
    for path in ["/JRADB/accessD.html","/JRADB/accessD.html?CNAME=pw01dde1006202604070120260921%2F88"]:
        try:
            urls += race_links(get(urljoin(BASE,path)))
        except Exception as e:
            print("index fetch failed:",e)

    # Deduplicate and try a larger number than the old version.
    urls=list(dict.fromkeys(urls))
    print("candidate race URLs:",len(urls))

    races=[]
    for u in urls[:100]:
        try:
            r=parse_race(u)
            if r:
                races.append(r)
        except Exception as e:
            print("skip",u,e)

    # Keep one record per venue/race number.
    seen=set(); unique=[]
    for r in races:
        key=(r["venue"],r["race_no"])
        if key not in seen:
            seen.add(key); unique.append(r)

    payload={
        "updated_at":datetime.now(JST).isoformat(),
        "source":"JRA",
        "races":unique
    }
    OUT.parent.mkdir(parents=True,exist_ok=True)
    OUT.write_text(json.dumps(payload,ensure_ascii=False,indent=2),encoding="utf-8")
    print("wrote",len(unique),"races")

if __name__=="__main__":
    main()
