#!/usr/bin/env python3
import datetime as dt
import html, json, re, time, urllib.parse, urllib.request
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed

BASE="https://www.jra.go.jp"
OUT=Path("data/races.json")
UA_LIST=[
 "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/140.0 Safari/537.36",
 "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/140.0 Safari/537.36",
]
VENUE_CODE={"札幌":"01","函館":"02","福島":"03","新潟":"04","東京":"05","中山":"06","中京":"07","京都":"08","阪神":"09","小倉":"10"}

# Seed URLs observed from JRA's own public race pages. The suffix is not calculated;
# we try a small known set first, then 00..FF only if needed.
KNOWN_SUFFIXES=["92","FC","B1","66","1B","D0","85","3A","E4","99","5C","71"]

def fetch(url, timeout=20):
    last=None
    for ua in UA_LIST:
        try:
            req=urllib.request.Request(url,headers={
                "User-Agent":ua,"Accept":"text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Accept-Language":"ja,en-US;q=0.8,en;q=0.5","Referer":"https://www.jra.go.jp/",
                "Connection":"close",
            })
            with urllib.request.urlopen(req,timeout=timeout) as r:
                return r.read().decode(r.headers.get_content_charset() or "utf-8","replace")
        except Exception as e:
            last=e
    raise last

def clean(s):
    return re.sub(r"\s+"," ",html.unescape(s)).strip()

def race_url(date, venue, kai, day, race_no, suffix):
    code=VENUE_CODE[venue]
    cname=f"pw01dde01{code}{date.year:04d}{kai:02d}{day:02d}{race_no:02d}{date:%Y%m%d}%2F{suffix.upper()}"
    return BASE+"/JRADB/accessD.html?CNAME="+cname

def valid(text,date,race_no,venue=None):
    t=clean(text)
    return (f"{date.year}年{date.month}月{date.day}日" in t
            and re.search(rf"{race_no}\s*レース",t) is not None
            and "出馬表" in t
            and (venue is None or venue in t))

def discover(date,venue,kai,day,race_no):
    # First try suffixes known from real JRA pages, then the remaining byte values.
    suffixes=[]
    for s in KNOWN_SUFFIXES+[f"{i:02X}" for i in range(256)]:
        if s not in suffixes: suffixes.append(s)
    def one(s):
        u=race_url(date,venue,kai,day,race_no,s)
        try:
            t=fetch(u,12)
            if valid(t,date,race_no,venue): return u,t
        except Exception: pass
        return None
    with ThreadPoolExecutor(max_workers=8) as ex:
        fs=[ex.submit(one,s) for s in suffixes]
        for f in as_completed(fs):
            x=f.result()
            if x:
                for q in fs: q.cancel()
                return x
    return None

def parse_links(text,date,venue):
    t=html.unescape(text)
    out={}
    for m in re.finditer(r'https?://www\.jra\.go\.jp/JRADB/accessD\.html\?CNAME=([^"\'>\s]+)',t):
        raw=m.group(1).replace("&amp;","&")
        u=BASE+"/JRADB/accessD.html?CNAME="+raw if not raw.startswith("http") else raw
        z=re.search(r'(?:^|[^\d])(\d{2})(\d{8})%2F([0-9A-Fa-f]{2})$',raw)
        if not z: z=re.search(r'(?:^|[^\d])(\d{2})(\d{8})/([0-9A-Fa-f]{2})$',urllib.parse.unquote(raw))
        if z:
            n=int(z.group(1))
            if 1<=n<=12: out[n]=u
    return out

def parse_race(text,url,date,venue,kai,day,n):
    t=clean(text)
    title=""
    # H1-like race title: the text immediately before age/class details.
    m=re.search(rf"{n}\s*レース.*?発走時刻[^ ]*\s+(?:Image:\s*)?(.+?)\s+(?=\d歳|[123]歳以上)",t)
    if m: title=clean(m.group(1))
    dm=re.search(r"コース[：:]\s*([\d,]+)\s*メートル（([^）]+)）",t)
    distance=dm.group(1).replace(",","")+"m" if dm else ""
    surface=dm.group(2) if dm else ""
    horses=[]
    # JRA flattened page text: number + name + odds(popularity)
    pat=re.compile(r"(?<!\d)(\d{1,2})\s+(.{1,80}?)\s+(\d+(?:\.\d+)?)\((\d+)番人気\)")
    seen=set()
    for m in pat.finditer(t):
        no=int(m.group(1))
        if no<1 or no>18: continue
        name=clean(m.group(2))
        name=re.sub(r"^(?:Image:\s*)?(?:ブリンカー着用\s*)?","",name)
        if not name or name in seen: continue
        seen.add(name)
        horses.append({"number":no,"name":name,"odds":float(m.group(3)),"popularity":int(m.group(4))})
    horses.sort(key=lambda x:x["number"])
    return {"date":date.isoformat(),"venue":venue,"meeting":kai,"meeting_day":day,
            "race_no":n,"name":title,"distance":distance,"surface":surface,
            "horses":horses,"source_url":url}

def main():
    # No calendar-page dependency: use the current day's official JRA race URLs.
    date=dt.date.today()
    # On days with normal JRA racing, current 2026 schedule around this date is
    # Nakayama 4/7 and Hanshin 4/7. We try every plausible venue and infer a valid one.
    candidates=[("中山",4,7),("阪神",4,7),("東京",4,7),("京都",4,7),("中京",3,7),("小倉",2,7),("札幌",2,7)]
    print("date=",date)
    allr=[]
    for venue,kai,day in candidates:
        seed=discover(date,venue,kai,day,1)
        if not seed:
            print("NO SEED",venue)
            continue
        u,t=seed
        print("SEED",venue,u)
        links=parse_links(t,date,venue); links[1]=u
        for n in range(2,13):
            if n not in links:
                x=discover(date,venue,kai,day,n)
                if x: links[n]=x[0]
        for n,u2 in sorted(links.items()):
            try:
                tt=t if n==1 else fetch(u2)
                r=parse_race(tt,u2,date,venue,kai,day,n)
                allr.append(r)
                print("OK",venue,f"{n}R","horses=",len(r["horses"]),r["distance"])
            except Exception as e: print("PARSE FAIL",venue,n,e)
        # Usually two venues; continue to collect both.
    payload={"updated_at":dt.datetime.now(dt.timezone(dt.timedelta(hours=9))).isoformat(),
             "source":"JRA","races":allr}
    OUT.parent.mkdir(parents=True,exist_ok=True)
    OUT.write_text(json.dumps(payload,ensure_ascii=False,indent=2),encoding="utf-8")
    print("wrote",len(allr),"races")

if __name__=="__main__": main()
