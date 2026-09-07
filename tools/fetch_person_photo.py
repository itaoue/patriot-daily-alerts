#!/usr/bin/env python3
"""
Fetch a licensed photo of a public figure from Wikimedia Commons and save it as a 1200x630 JPEG.

    python tools/fetch_person_photo.py --query "JD Vance 2025" --out src/static/uploads/2026/09/slug.jpg
    python tools/fetch_person_photo.py --query "Mike Johnson speaker" --list

Only public-domain, CC0, CC BY and CC BY-SA files are accepted; the credit line is printed
(and returned by fetch()) so it can be stored with the story.
"""
import argparse
import html as htmlmod
import json
import pathlib
import re
import sys
import urllib.parse
import urllib.request

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from imgfit import fit  # noqa: E402

UA = {"User-Agent": "PatriotDailyAlerts/1.0 (https://patriotdailyalerts.com; news@patriotdailyalerts.com)"}
OK = ("Public domain", "CC0", "CC BY 2.0", "CC BY 2.5", "CC BY 3.0", "CC BY 4.0",
      "CC BY-SA 2.0", "CC BY-SA 2.5", "CC BY-SA 3.0", "CC BY-SA 4.0")


def api(params):
    url = "https://commons.wikimedia.org/w/api.php?" + urllib.parse.urlencode({**params, "format": "json"})
    with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=60) as r:
        return json.load(r)


def candidates(query, limit=12):
    res = api({"action": "query", "list": "search", "srsearch": f"{query} filetype:bitmap", "srnamespace": 6, "srlimit": limit})
    titles = [h["title"] for h in res.get("query", {}).get("search", []) if h["title"].lower().endswith((".jpg", ".jpeg", ".png"))]
    if not titles:
        return []
    info = api({"action": "query", "titles": "|".join(titles[:limit]), "prop": "imageinfo",
                "iiprop": "url|size|extmetadata", "iiurlwidth": 1600})
    out = []
    for page in info.get("query", {}).get("pages", {}).values():
        ii = (page.get("imageinfo") or [{}])[0]
        meta = ii.get("extmetadata", {})
        lic = meta.get("LicenseShortName", {}).get("value", "")
        if not any(lic.startswith(ok) for ok in OK):
            continue
        if ii.get("width", 0) < 800:
            continue
        artist = re.sub(r"<[^>]+>", "", meta.get("Artist", {}).get("value", "")).strip()
        out.append({"title": page["title"], "url": ii.get("thumburl") or ii["url"], "license": lic,
                    "credit": htmlmod.unescape(f"{artist}, {lic}, via Wikimedia Commons" if artist else f"{lic}, via Wikimedia Commons"),
                    "width": ii.get("width"), "height": ii.get("height")})
    # prefer landscape-ish, larger images
    out.sort(key=lambda c: (-(c["width"] >= c["height"]), -(c["width"] or 0)))
    return out


def fetch(query, out_path, anchor=0.2):
    cands = candidates(query)
    if not cands:
        return None
    c = cands[0]
    out_path = pathlib.Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with urllib.request.urlopen(urllib.request.Request(c["url"], headers=UA), timeout=90) as r:
        out_path.write_bytes(r.read())
    fit(out_path, 1200, 630, anchor)
    return c["credit"]


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--query", required=True)
    ap.add_argument("--out")
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--anchor", type=float, default=0.2)
    a = ap.parse_args()
    if a.list or not a.out:
        for c in candidates(a.query):
            print(f"{c['width']}x{c['height']} {c['license']:<12} {c['title']}")
        sys.exit(0)
    credit = fetch(a.query, a.out, a.anchor)
    print(credit or "no suitable licensed photo found")
    sys.exit(0 if credit else 1)
