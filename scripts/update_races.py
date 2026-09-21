import json, re, html
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.request import Request, urlopen

JST=timezone(timedelta(hours=9))
BASE="https://www.jra.go.jp"
OUT=Path("data/races.json")

# JRA venue codes used in the accessD CNAME.
VENUE_CODES={
    "札幌":"01","函館":"02","福島":"03","新潟":"04","東京":"05",
    "中山":"06","中京":"07","京都":"08","阪神":"09","小倉":"10"
}

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

def suffix(race_no):
    # JRA CNAME trailing identifier observed on the official race pages.
    # It advances by -0x4B for each race number, modulo 256.
    return f"{(0x92 - 0x4B*(race_no-1)) & 0xff:02X}"

def make_url(venue, meeting_day, race_no, date):
    code=VENUE_CODES[venue]
    ymd=date.strftime("%Y%m%d")
    # e.g. pw01dde0109202604070120260921/92
    cname=f"pw01dde01{code}{ymd}{meeting_day:04d}{race_no:02d}{ymd}/{suffix(race_no)}"
    return f"{BASE}/JRADB/accessD.html?CNAME={cname}"

def parse_race(url):
    raw=get(url)
    text=clean(raw)
    m=re.search(r"(\d{4}年\d{1,2}月\d{1,2}日).*?(\d{1,2})レース",text)
    if not m:
        return None

    race_no=int(m.group(2))
    vm=re.search(r"\d{4}年\d{1,2}月\d{1,2}日\s+(\d+回)([^\s]+?)(\d+)日",text)
    venue=vm.group(2) if vm else "JRA"
    meeting_day=int(vm.group(3)) if vm else 0

    tm=re.search(r"発走時刻：\d{1,2}時\d{2}分\s*(.*?)\s*(?:\d+歳|2歳|3歳|4歳以上|サラ系)",text)
    title=tm.group(1).strip() if tm else f"{race_no}R"

    cm=re.search(r"コース：\s*([\d,]+)メートル（(芝|ダート)[^）]*）",text)
    distance=cm.group(1).replace(",","")+"m" if cm else ""
    surface=cm.group(2) if cm else ""
    track=re.search(r"(芝|ダート)(良|稍重|重|不良)",text)
    condition=track.group(2) if track else "未取得"

    horses=[]
    # Official page text has: 馬番 + 馬名 + オッズ(人気)
    pat=re.compile(r"(?:枠\d+\s*)?(\d{1,2})\s+([^\d\s]{2,30}?)(\d+(?:\.\d+))\((\d+)番人気\)")
    for mm in pat.finditer(text):
        no=int(mm.group(1))
        name=mm.group(2).strip()
        if no<=18 and not any(h["horse_no"]==no for h in horses):
            horses.append({
                "horse_no":no,
                "horse":name,
                "odds":float(mm.group(3)),
                "popularity":int(mm.group(4))
            })

    return {
        "venue":venue,
        "meeting_day":meeting_day,
        "race_no":race_no,
        "name":title,
        "distance":distance,
        "surface":surface,
        "track_condition":condition,
        "horses":horses,
        "predictions":[],
        "commentary":"JRA公式出馬表から取得。予測エンジンは次の段階で追加。",
        "source_url":url
    }

def discover_meetings(date):
    # Try the official race-selection page and extract strings such as
    # 「4回阪神7日」「4回中山7日」. We only need the meeting number/day.
    try:
        raw=get(f"{BASE}/JRADB/accessD.html")
        text=clean(raw)
        found=[]
        for m in re.finditer(r"(\d+)回(札幌|函館|福島|新潟|東京|中山|中京|京都|阪神|小倉)(\d+)日",text):
            venue=m.group(2); meeting=int(m.group(1)); day=int(m.group(3))
            item=(venue,meeting,day)
            if item not in found: found.append(item)
        if found: return found
    except Exception as e:
        print("schedule page failed:",e)

    # Current-day fallback. For dates where JRA's schedule page is inaccessible,
    # the workflow can be updated with the day's meeting metadata.
    return []

def main():
    today=datetime.now(JST).date()
    meetings=discover_meetings(today)
    print("meetings:",meetings)

    races=[]
    # Each meeting normally has up to 12 races.
    for venue,meeting,day in meetings:
        meeting_day=meeting*100+day
        for race_no in range(1,13):
            url=make_url(venue,meeting_day,race_no,today)
            try:
                r=parse_race(url)
                if r and r["horses"]:
                    races.append(r)
                    print("OK",venue,race_no,len(r["horses"]))
            except Exception as e:
                print("skip",venue,race_no,e)

    payload={
        "updated_at":datetime.now(JST).isoformat(),
        "source":"JRA",
        "races":races
    }
    OUT.parent.mkdir(parents=True,exist_ok=True)
    OUT.write_text(json.dumps(payload,ensure_ascii=False,indent=2),encoding="utf-8")
    print("wrote",len(races),"races")

if __name__=="__main__":
    main()
