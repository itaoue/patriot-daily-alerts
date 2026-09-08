#!/usr/bin/env python3
"""
Topic radar: what the conservative press is talking about right now.

    python tools/radar.py                 # last 2 days -> content/radar/<date>.json + .md
    python tools/radar.py --days 3 --print
    python tools/radar.py --site https://patriotdailyalerts.com   # also skip stories we already ran

Reads the RSS feeds of the source outlets (Google News "site:" search is used for any feed
that blocks scripts), plus Google News queries for a watchlist of public figures. Stories
covered by two or more outlets are clustered into one candidate and ranked by how many
outlets ran it, how fresh it is, and whether it names a watchlist figure. Candidates that
look like a story already on the site (via /api/posts/recent) are flagged `written`.

Standard library only.
"""
import argparse
import datetime as dt
import email.utils
import html
import json
import os
import pathlib
import re
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET

ROOT = pathlib.Path(__file__).resolve().parent.parent
RADAR = ROOT / "content" / "radar"
UA = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_0) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0 Safari/537.36",
      "Accept": "application/rss+xml, application/xml, text/xml, */*;q=0.8"}

# outlet -> (rss url or None, site domain for Google News fallback)
SOURCES = {
    "Newsmax": ("https://www.newsmax.com/rss/Newsfront/1/", "newsmax.com"),
    "Gateway Pundit": ("https://www.thegatewaypundit.com/feed/", "thegatewaypundit.com"),
    "Western Journal": ("https://www.westernjournal.com/feed/", "westernjournal.com"),
    "National Review": ("https://www.nationalreview.com/feed/", "nationalreview.com"),
    "Washington Examiner": ("https://www.washingtonexaminer.com/feed", "washingtonexaminer.com"),
    "Fox News": ("https://moxie.foxnews.com/google-publisher/politics.xml", "foxnews.com"),
    "Daily Wire": ("https://www.dailywire.com/feeds/rss.xml", "dailywire.com"),
    "Breitbart": ("https://feeds.feedburner.com/breitbart", "breitbart.com"),
    "New York Post": ("https://nypost.com/politics/feed/", "nypost.com"),
    "Daily Caller": ("https://dailycaller.com/feed/", "dailycaller.com"),
    "The Federalist": ("https://thefederalist.com/feed/", "thefederalist.com"),
    "Just the News": ("https://justthenews.com/rss.xml", "justthenews.com"),
    "Fox News US": ("https://moxie.foxnews.com/google-publisher/us.xml", "foxnews.com/us"),   # crime, courts, culture
    "New York Post News": ("https://nypost.com/news/feed/", "nypost.com/news"),
}
PEOPLE = [
    "Donald Trump", "JD Vance", "Elon Musk", "Mike Johnson", "John Thune", "Chuck Schumer", "Hakeem Jeffries",
    "Gavin Newsom", "Alexandria Ocasio-Cortez", "Pete Hegseth", "Marco Rubio", "Pam Bondi", "Kash Patel",
    "Kristi Noem", "Tom Homan", "RFK Jr.", "Tulsi Gabbard", "Scott Bessent", "Jerome Powell", "Supreme Court",
    "Ron DeSantis", "Nancy Pelosi", "Zohran Mamdani", "Letitia James", "Zelensky", "Netanyahu", "Xi Jinping",
]
CATEGORIES = {  # category -> title regex (first match wins, politics is the default)
    "border": r"\b(border|immigra\w*|ice\b|deport\w*|migrant\w*|asylum|cartel\w*|sanctuary|illegal alien\w*|cbp)\b",
    "crime": r"\b(murder\w*|kill\w*|homicide|shooting|shot dead|stabb\w*|manhunt|arrest\w*|charged with|indict\w*|sentenc\w*|death penalty|verdict|jury|juror|trial|mistrial|convict\w*|guilty|fugitive|kidnap\w*|assault\w*|police officer|cops?\b|sheriff|suspect|inmate|prison|carjack\w*|robber\w*)\b",
    "economy": r"\b(econom\w*|inflation|tariff\w*|jobs?\b|unemployment|fed\b|interest rate\w*|tax\w*|stock\w*|market\w*|prices?|gas price\w*|budget|deficit|debt|dollar|wall street|gdp|recession|social security|medicare)\b",
    "world": r"\b(iran|israel|gaza|hamas|ukraine|russia|putin|zelensky|china|taiwan|xi\b|nato|north korea|venezuela|mexico|europe|uk\b|britain|canada|foreign|war\b|strike\w* on|troops|military|pentagon)\b",
    "culture": r"\b(school\w*|teacher\w*|college\w*|universit\w*|campus|woke|dei\b|trans\w*|gender|church\w*|christian\w*|faith|hollywood|celebrit\w*|nfl|nba|mlb|wnba|ncaa|espn|umpire|referee|quarterback|coach|mascot|super bowl|world series|olympic\w*|royal\w*|king charles|prince|princess|game show|reality tv|netflix|disney|abortion|pro-life|gun\w*|second amendment|censor\w*|free speech|media|cnn|msnbc|nbc|abc news|new york times|disney|netflix|late night|kimmel|colbert)\b",
}
STOP = set("the a an and or of to in on for with by at from as is are was were be been this that these those it its into over after before about against amid"
           " says said say new just how why what who will would could should can may might his her their our your than then them they he she we you not no"
           " has have had do does did up out off more most one two three first last next big top news report reports reported".split())


def fetch(url, timeout=25):
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def parse_feed(raw, outlet):
    items = []
    try:
        root = ET.fromstring(raw)
    except ET.ParseError:
        return items
    ns = {"atom": "http://www.w3.org/2005/Atom"}
    for it in root.iter("item"):  # RSS
        title = html.unescape((it.findtext("title") or "").strip())
        link = (it.findtext("link") or "").strip()
        date = it.findtext("pubDate") or it.findtext("{http://purl.org/dc/elements/1.1/}date") or ""
        src = it.find("source")
        publisher = src.text.strip() if src is not None and src.text else outlet
        if title.endswith(" - " + publisher):
            title = title[: -len(publisher) - 3].rstrip()
        items.append({"title": title, "url": link, "published": to_iso(date), "outlet": publisher})
    for e in root.findall("atom:entry", ns):  # Atom
        title = html.unescape((e.findtext("atom:title", default="", namespaces=ns) or "").strip())
        link_el = e.find("atom:link", ns)
        link = link_el.get("href", "") if link_el is not None else ""
        date = e.findtext("atom:published", default="", namespaces=ns) or e.findtext("atom:updated", default="", namespaces=ns)
        items.append({"title": title, "url": link, "published": to_iso(date), "outlet": outlet})
    return [i for i in items if i["title"] and i["url"]]


def to_iso(s):
    s = (s or "").strip()
    if not s:
        return ""
    try:
        d = email.utils.parsedate_to_datetime(s)
    except (TypeError, ValueError):
        try:
            d = dt.datetime.fromisoformat(s.replace("Z", "+00:00"))
        except ValueError:
            return ""
    if d.tzinfo is None:
        d = d.replace(tzinfo=dt.timezone.utc)
    return d.astimezone(dt.timezone.utc).isoformat(timespec="minutes")


def google_news(query, days):
    q = urllib.parse.quote(f"{query} when:{days}d")
    return f"https://news.google.com/rss/search?q={q}&hl=en-US&gl=US&ceid=US:en"


def tokens(title):
    return {w for w in re.findall(r"[a-z0-9']+", title.lower()) if len(w) > 2 and w not in STOP}


def similar(a, b):
    if not a or not b:
        return 0.0
    inter = len(a & b)
    return inter / len(a | b) if inter >= 2 else 0.0


def categorize(text):
    t = text.lower()
    for cat, rx in CATEGORIES.items():
        if re.search(rx, t):
            return cat
    return "politics"


def people_in(text):
    t = text.lower()
    found = []
    for p in PEOPLE:
        last = p.split()[-1].lower().rstrip(".")
        if p.lower() in t or (len(last) > 4 and re.search(r"\b" + re.escape(last) + r"\b", t)):
            found.append(p)
    return found


def collect(days):
    items, notes = [], []
    for outlet, (rss, site) in SOURCES.items():
        got = []
        if rss:
            try:
                got = parse_feed(fetch(rss), outlet)
            except (urllib.error.URLError, urllib.error.HTTPError, OSError, ValueError) as exc:
                notes.append(f"{outlet}: feed failed ({str(exc)[:60]}), using Google News")
        if not got:
            try:
                got = parse_feed(fetch(google_news(f"site:{site}", days)), outlet)
                for g in got:
                    g["outlet"] = outlet
            except (urllib.error.URLError, OSError, ValueError) as exc:
                notes.append(f"{outlet}: Google News failed ({str(exc)[:60]})")
        notes.append(f"{outlet}: {len(got)}")
        items += got
    for person in PEOPLE:
        try:
            for g in parse_feed(fetch(google_news(f'"{person}"', days)), "Google News"):
                g["via_person"] = person
                items.append(g)
        except (urllib.error.URLError, OSError, ValueError):
            pass
    cutoff = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=days)).isoformat()
    fresh = [i for i in items if not i["published"] or i["published"] >= cutoff]
    seen, out = set(), []
    for i in fresh:
        key = (i["title"].lower(), i["outlet"])
        if key not in seen:
            seen.add(key)
            out.append(i)
    return out, notes


def cluster(items):
    clusters = []
    for it in items:
        tk = tokens(it["title"])
        it["tokens"] = tk
        best, best_sim = None, 0.0
        for c in clusters:
            s = similar(tk, c["tokens"])
            if s > best_sim:
                best, best_sim = c, s
        if best and best_sim >= 0.34:
            best["items"].append(it)
            best["tokens"] |= tk
        else:
            clusters.append({"items": [it], "tokens": set(tk)})
    # second pass: merge clusters that turned out to be the same story (e.g. two phrasings of one headline)
    merged = []
    for c in clusters:
        target = next((m for m in merged if similar(c["tokens"], m["tokens"]) >= 0.3), None)
        if target:
            target["items"] += c["items"]
            target["tokens"] |= c["tokens"]
        else:
            merged.append(c)
    clusters = merged
    now = dt.datetime.now(dt.timezone.utc)
    out = []
    for c in clusters:
        outlets = {}
        for it in c["items"]:
            outlets.setdefault(it["outlet"], it)
        lead = max(c["items"], key=lambda i: (i["outlet"] in SOURCES, i["published"]))
        newest = max((i["published"] for i in c["items"] if i["published"]), default="")
        age_h = (now - dt.datetime.fromisoformat(newest)).total_seconds() / 3600 if newest else 48
        people = sorted({p for i in c["items"] for p in people_in(i["title"])} | {i["via_person"] for i in c["items"] if i.get("via_person")})
        source_outlets = [o for o in outlets if o in SOURCES]
        others = min(4, len(outlets) - len(source_outlets))  # syndicated local stations should not swamp the score
        score = 10 * len(source_outlets) + 3 * others + (6 if age_h < 12 else 3 if age_h < 24 else 0) + (3 if people else 0)
        out.append({
            "title": lead["title"], "category": categorize(" ".join(i["title"] for i in c["items"])), "people": people,
            "score": score, "newest": newest,
            "outlets": sorted(({"name": o, "title": i["title"], "url": i["url"], "published": i["published"]} for o, i in outlets.items()),
                              key=lambda o: o["name"] not in SOURCES),  # our source outlets first
        })
    out.sort(key=lambda c: (-c["score"], c["newest"]), reverse=False)
    return out


def already_written(site, token, days=14):
    titles = []
    if site:
        try:
            req = urllib.request.Request(f"{site.rstrip('/')}/api/posts/recent?days={days}", headers={**UA, "Authorization": f"Bearer {token}"})
            with urllib.request.urlopen(req, timeout=20) as r:
                titles = [p["title"] for p in json.load(r)["posts"]]
        except (urllib.error.URLError, OSError, ValueError, KeyError) as exc:
            print(f"warning: could not read published stories from {site}: {exc}")
    used = RADAR / "used.json"
    if used.exists():
        titles += json.loads(used.read_text()).get("titles", [])
    return [tokens(t) for t in titles]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=2)
    ap.add_argument("--site", default=os.environ.get("SITE_URL", ""))
    ap.add_argument("--token", default=os.environ.get("PUBLISH_TOKEN", ""))
    ap.add_argument("--out", help="output path without extension (default content/radar/<date>)")
    ap.add_argument("--print", action="store_true")
    a = ap.parse_args()
    items, notes = collect(a.days)
    clusters = cluster(items)
    done = already_written(a.site, a.token) if (a.site and a.token) or (RADAR / "used.json").exists() else []
    for i, c in enumerate(clusters, 1):
        c["rank"] = i
        c["written"] = any(similar(tokens(c["title"]), d) >= 0.5 for d in done)
    date = dt.date.today().isoformat()
    base = pathlib.Path(a.out) if a.out else RADAR / date
    base.parent.mkdir(parents=True, exist_ok=True)
    base.with_suffix(".json").write_text(json.dumps({"date": date, "days": a.days, "feeds": notes, "clusters": clusters}, indent=1, ensure_ascii=False))
    lines = [f"# Topic radar {date} (last {a.days} days, {len(items)} items, {len(clusters)} clusters)", "", "Feeds: " + "; ".join(notes), ""]
    for c in clusters[:60]:
        who = f" ★ {', '.join(c['people'])}" if c["people"] else ""
        flag = " (already covered)" if c["written"] else ""
        lines.append(f"- [{c['rank']}] {c['score']:>3} {c['category']:<8} {c['title']}{who}{flag}")
        for o in c["outlets"][:6]:
            lines.append(f"    - {o['name']}: {o['title'][:90]} <{o['url']}>")
    base.with_suffix(".md").write_text("\n".join(lines) + "\n")
    print(f"{len(items)} items -> {len(clusters)} clusters; wrote {base.with_suffix('.json').relative_to(ROOT)}")
    if a.print:
        print("\n".join(lines[3:63]))


if __name__ == "__main__":
    main()
