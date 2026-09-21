#!/usr/bin/env python3
"""
JRA race-card collector v4

Strategy:
1. Read JRA's official daily race-program page for today's date.
2. Extract today's venues and "meeting/day" values (e.g. 4回阪神7日).
3. Build the stable CNAME prefix used by JRA.
4. Discover the final 2-digit CNAME suffix by trying 00..FF for race 1.
5. Once one real race URL is found, fetch races 1..12 using the discovered
   suffixes from the page's own race-navigation links when possible.
6. Fall back to brute-force suffix discovery for individual races.
7. Write data/races.json.

No third-party racing API is used.
"""

import datetime as dt
import html
import json
import re
import time
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

BASE = "https://www.jra.go.jp"
CALENDAR = BASE + "/keiba/calendar2026/2026/{month:02d}/{day:02d}.html"
OUT = Path("data/races.json")

VENUE_CODE = {
    "札幌": "01", "函館": "02", "福島": "03", "新潟": "04",
    "東京": "05", "中山": "06", "中京": "07", "京都": "08",
    "阪神": "09", "小倉": "10",
}

HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; KeibaDataCollector/4.0; +https://github.com/desumosan/Keiba)"
}

def fetch(url, timeout=20):
    req = urllib.request.Request(url, headers=HEADERS)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        raw = r.read()
        charset = r.headers.get_content_charset() or "utf-8"
        return raw.decode(charset, errors="replace")

def clean(s):
    s = html.unescape(s)
    s = re.sub(r"\s+", " ", s)
    return s.strip()

def daily_program():
    today = dt.date.today()
    url = CALENDAR.format(month=today.month, day=today.day)
    text = fetch(url)
    # The official page contains headings such as "4回阪神7日".
    venues = []
    for m in re.finditer(r"(\d+)回(札幌|函館|福島|新潟|東京|中山|中京|京都|阪神|小倉)(\d+)日", text):
        kai, venue, day = int(m.group(1)), m.group(2), int(m.group(3))
        item = (venue, kai, day)
        if item not in venues:
            venues.append(item)
    return today, venues

def cname_prefix(date, venue, kai, kaisai_day, race_no):
    # Confirmed JRA pattern, e.g.
    # pw01dde0109202604070120260921/92
    code = VENUE_CODE[venue]
    return (
        f"pw01dde01{code}"
        f"{date.year:04d}{kai:02d}{kaisai_day:02d}"
        f"{race_no:02d}{date:%Y%m%d}"
    )

def candidate_url(prefix, suffix):
    return BASE + "/JRADB/accessD.html?CNAME=" + urllib.parse.quote(
        prefix + "/" + f"{suffix:02X}", safe=""
    )

def looks_like_race_page(text, date, race_no):
    t = clean(text)
    return (
        f"{date.year}年{date.month}月{date.day}日" in t
        and re.search(rf"{race_no}\s*レース", t) is not None
        and "出馬表" in t
    )

def try_suffix(prefix, suffix, date, race_no):
    url = candidate_url(prefix, suffix)
    try:
        text = fetch(url, timeout=12)
        if looks_like_race_page(text, date, race_no):
            return url, text
    except Exception:
        pass
    return None

def discover_race_url(prefix, date, race_no):
    # 256 requests, but only for a race for which no link was found.
    # Limit concurrency to avoid hammering JRA.
    with ThreadPoolExecutor(max_workers=16) as ex:
        futures = [ex.submit(try_suffix, prefix, s, date, race_no)
                   for s in range(256)]
        for fut in as_completed(futures):
            result = fut.result()
            if result:
                for f in futures:
                    f.cancel()
                return result
    return None

def extract_race_links(text, date):
    # Handles both raw and HTML-escaped URLs.
    t = html.unescape(text)
    links = re.findall(
        r'(?:href=["\']|https?://www\.jra\.go\.jp/JRADB/accessD\.html\?CNAME=)'
        r'([^"\'>\s]+)',
        t,
        flags=re.I
    )
    out = {}
    for raw in links:
        if raw.startswith("http"):
            url = raw
        else:
            url = BASE + "/JRADB/accessD.html?CNAME=" + raw
        if "CNAME=" not in url:
            continue
        m = re.search(r"(\d{2})(\d{8})%2F([0-9A-Fa-f]{2})$", urllib.parse.unquote(url))
        if not m:
            m = re.search(r"(\d{2})(\d{8})/([0-9A-Fa-f]{2})$", urllib.parse.unquote(url))
        if not m:
            continue
        race_no = int(m.group(1))
        if race_no >= 1 and race_no <= 12:
            out[race_no] = url
    return out

def extract_field(text):
    t = clean(text)
    race_name = ""
    distance = ""
    surface = ""
    m = re.search(r"##\s*([^\n]+)", t)
    # Fallback: find text after "発走時刻" and before "2歳/3歳..."
    m2 = re.search(r"発走時刻[：:]\s*[^ ]+\s+([^\n]+?)\s+(?:\d歳|[123]歳以上)", t)
    if m2:
        race_name = clean(m2.group(1))
    cm = re.search(r"コース[：:]\s*([\d,]+)\s*メートル（([^）]+)）", t)
    if cm:
        distance = cm.group(1).replace(",", "") + "m"
        surface = cm.group(2)
    return race_name, distance, surface

def extract_horses(text):
    # JRA's table is flattened in some environments. This heuristic targets
    # "馬名 12.3(4番人気)" patterns and de-duplicates by horse name.
    t = clean(text)
    horses = []
    seen = set()
    pat = re.compile(r"(?<!\d)(\d{1,2})\s+([^\n]{1,60}?)\s+(\d+(?:\.\d+)?)\((\d+)番人気\)")
    for m in pat.finditer(t):
        no = int(m.group(1))
        if not 1 <= no <= 18:
            continue
        raw = clean(m.group(2))
        raw = re.sub(r"^(?:Image:\s*)?(?:ブリンカー着用\s*)?", "", raw)
        raw = raw.strip(" /")
        # Avoid matching labels/metadata.
        if not raw or any(x in raw for x in ("番人気", "コース", "本賞金", "発走時刻")):
            continue
        if raw in seen:
            continue
        seen.add(raw)
        horses.append({
            "number": no,
            "name": raw,
            "odds": float(m.group(3)),
            "popularity": int(m.group(4)),
        })
    horses.sort(key=lambda x: x["number"])
    return horses

def parse_race(text, url, date, venue, kai, day, race_no):
    name, distance, surface = extract_field(text)
    horses = extract_horses(text)
    return {
        "date": date.isoformat(),
        "venue": venue,
        "meeting": kai,
        "meeting_day": day,
        "race_no": race_no,
        "name": name,
        "distance": distance,
        "surface": surface,
        "horses": horses,
        "source_url": url,
    }

def main():
    date, venues = daily_program()
    print(f"date={date}")
    print(f"meetings={venues}")

    all_races = []

    for venue, kai, day in venues:
        print(f"--- {venue} {kai}回{day}日 ---")
        first_prefix = cname_prefix(date, venue, kai, day, 1)

        # Find a real race-1 page.
        found = discover_race_url(first_prefix, date, 1)
        if not found:
            print(f"FAIL seed: {venue}")
            continue

        seed_url, seed_html = found
        print(f"OK seed: {seed_url}")

        links = extract_race_links(seed_html, date)
        links[1] = seed_url

        # If the page did not expose navigation links, discover each race.
        for race_no in range(1, 13):
            if race_no not in links:
                prefix = cname_prefix(date, venue, kai, day, race_no)
                found_race = discover_race_url(prefix, date, race_no)
                if found_race:
                    links[race_no] = found_race
                    print(f"OK discovered {venue} {race_no}R")
                else:
                    print(f"FAIL {venue} {race_no}R")

        # Fetch and parse all discovered race pages.
        for race_no in sorted(links):
            try:
                race_html = seed_html if race_no == 1 else fetch(links[race_no])
                item = parse_race(
                    race_html, links[race_no], date, venue, kai, day, race_no
                )
                all_races.append(item)
                print(
                    f"OK {venue} {race_no}R "
                    f"horses={len(item['horses'])} "
                    f"distance={item['distance']}"
                )
                time.sleep(0.2)
            except Exception as e:
                print(f"PARSE FAIL {venue} {race_no}R: {e}")

    OUT.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "updated_at": dt.datetime.now(dt.timezone(dt.timedelta(hours=9))).isoformat(),
        "source": "JRA",
        "races": all_races,
    }
    OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"wrote {len(all_races)} races -> {OUT}")

if __name__ == "__main__":
    main()
