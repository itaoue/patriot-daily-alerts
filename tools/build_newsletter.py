#!/usr/bin/env python3
"""
Build the Patriot Daily Alerts email newsletter (AM / PM edition) from the site's published stories.

    python tools/build_newsletter.py                 # edition picked from the clock (ET): AM before noon, else PM
    python tools/build_newsletter.py --edition pm --date 2026-09-08
    python tools/build_newsletter.py --hours 24      # look back further for lead stories

Layout mirrors the Middle America News daily: one 600px column, dark masthead with the logo,
edition + date strip, three lead stories (headline as an underlined link, full-width image,
red READ MORE button), an "Also Trending" list of headlines, an optional SPONSORED block, and
a quiet grey footer with unsubscribe / view-online merge tags (BigMailer style: *|UNSUB|*, *|VIEW|*).

Stories come from GET /api/posts/recent (PUBLISH_TOKEN) — only published ones. Settings and the
optional sponsor live in content/newsletter/config.json.

Output: dist/newsletters/<date>-<edition>.html / .txt / .json, and a copy of the HTML in
src/static/newsletters/ so "View online" works once committed.
"""
import argparse
import datetime as dt
import html
import json
import os
import pathlib
import shutil
import sys
import zoneinfo

import requests

ROOT = pathlib.Path(__file__).resolve().parent.parent
OUT = ROOT / "dist" / "newsletters"
STATIC = ROOT / "src" / "static" / "newsletters"
CONFIG = json.loads((ROOT / "content" / "newsletter" / "config.json").read_text())
SITE = os.environ.get("SITE_URL", "https://patriotdailyalerts.com").rstrip("/")
ET = zoneinfo.ZoneInfo("America/New_York")

NAVY, RED, INK, GREY, RULE, BG, PAPER = "#0C1F3D", "#B3202E", "#000000", "#666666", "#dddddd", "#e6e8ec", "#ffffff"
FONT = "Helvetica,Arial,sans-serif"


def esc(s):
    return html.escape(s or "", quote=True)


def fetch_stories(token, days=3):
    r = requests.get(f"{SITE}/api/posts/recent?days={days}", headers={"Authorization": f"Bearer {token}", "User-Agent": "Mozilla/5.0"}, timeout=30)
    r.raise_for_status()
    posts = [p for p in r.json()["posts"] if p["status"] == "published"]
    posts.sort(key=lambda p: p["published_at"], reverse=True)
    return posts


def pick(posts, hours, leads_n, trending_n):
    cutoff = (dt.datetime.utcnow() - dt.timedelta(hours=hours)).isoformat()
    fresh = [p for p in posts if p["published_at"] >= cutoff]
    with_img = [p for p in fresh if p.get("image_url")] + [p for p in fresh if not p.get("image_url")]
    leads = with_img[:leads_n]
    if len(leads) < leads_n:  # quiet day: fall back to the newest published stories
        leads += [p for p in posts if p not in leads and p.get("image_url")][: leads_n - len(leads)]
    trending = [p for p in posts if p not in leads][:trending_n]
    return leads, trending


def link(p, campaign):
    return f"{SITE}{p['url']}?utm_source=newsletter&utm_medium=email&utm_campaign={campaign}"


def story_block(p, campaign):
    u = link(p, campaign)
    img = (f'<tr><td style="padding:0 25px 6px;"><a href="{u}" target="_blank"><img src="{esc(p["image_url"])}" width="550" alt="{esc(p["title"])}" '
           f'style="display:block;width:100%;max-width:550px;height:auto;border:0;outline:none;text-decoration:none;"></a></td></tr>' if p.get("image_url") else "")
    return f'''
<tr><td style="padding:22px 25px 6px;font-family:{FONT};font-size:26px;line-height:120%;font-weight:700;color:{INK};text-align:center;">
  <a href="{u}" target="_blank" style="color:{INK};text-decoration:underline;">{esc(p["title"])}</a></td></tr>
{img}
<tr><td align="center" style="padding:10px 25px 24px;">
  <table role="presentation" border="0" cellpadding="0" cellspacing="0"><tr>
    <td align="center" bgcolor="{RED}" style="background:{RED};border-radius:3px;padding:10px 28px;">
      <a href="{u}" target="_blank" style="font-family:{FONT};font-size:18px;line-height:120%;color:#ffffff;text-decoration:none;font-weight:700;letter-spacing:.02em;">READ MORE</a>
    </td></tr></table></td></tr>
<tr><td style="padding:0 25px;"><div style="border-top:1px solid {RULE};font-size:0;line-height:0;">&nbsp;</div></td></tr>'''


def trending_block(items, campaign):
    if not items:
        return ""
    rows = "".join(
        f'<tr><td style="padding:10px 0;border-bottom:1px solid {RULE};font-family:{FONT};font-size:16px;line-height:130%;font-weight:700;">'
        f'<a href="{link(p, campaign)}" target="_blank" style="color:{NAVY};text-decoration:none;">{esc(p["title"])}</a></td></tr>'
        for p in items)
    return f'''
<tr><td style="padding:22px 25px 4px;font-family:{FONT};font-size:20px;line-height:120%;font-weight:700;color:{INK};text-transform:uppercase;letter-spacing:.04em;">
  <span style="border-bottom:3px solid {RED};padding-bottom:4px;">Also Trending</span></td></tr>
<tr><td style="padding:6px 25px 20px;"><table role="presentation" width="100%" border="0" cellpadding="0" cellspacing="0">{rows}</table></td></tr>'''


def sponsor_block(s):
    if not s or not s.get("enabled"):
        return ""
    img = (f'<tr><td style="padding:0 25px 6px;"><a href="{esc(s["url"])}" target="_blank"><img src="{esc(s["image"])}" width="550" alt="" '
           f'style="display:block;width:100%;max-width:550px;height:auto;border:0;"></a></td></tr>' if s.get("image") else "")
    return f'''
<tr><td style="padding:18px 25px 0;font-family:{FONT};font-size:11px;line-height:120%;color:{GREY};text-align:center;letter-spacing:.12em;text-transform:uppercase;">{esc(s.get("label", "SPONSORED"))}</td></tr>
<tr><td style="padding:8px 25px 6px;font-family:{FONT};font-size:22px;line-height:120%;font-weight:700;color:{INK};text-align:center;">
  <a href="{esc(s["url"])}" target="_blank" style="color:{INK};text-decoration:underline;">{esc(s["headline"])}</a></td></tr>
{img}
<tr><td align="center" style="padding:8px 25px 22px;">
  <table role="presentation" border="0" cellpadding="0" cellspacing="0"><tr>
    <td align="center" bgcolor="{NAVY}" style="background:{NAVY};border-radius:3px;padding:10px 28px;">
      <a href="{esc(s["url"])}" target="_blank" style="font-family:{FONT};font-size:16px;color:#ffffff;text-decoration:none;font-weight:700;">{esc(s.get("cta", "LEARN MORE"))}</a>
    </td></tr></table></td></tr>
<tr><td style="padding:0 25px;"><div style="border-top:1px solid {RULE};font-size:0;line-height:0;">&nbsp;</div></td></tr>'''


def render(leads, trending, edition, date_et, campaign, view_url):
    subject = leads[0]["title"]
    preheader = f"and {leads[1]['title']}" if len(leads) > 1 else CONFIG["tagline"]
    pad = "&#8204;&nbsp;" * 90  # keeps inbox previews from pulling in body text after the preheader
    stories = "".join(story_block(p, campaign) for p in leads)
    blocks = stories
    if CONFIG.get("sponsor", {}).get("enabled"):
        # sponsor after the first story, like the template's ad slot
        first_end = stories.find("</td></tr>", stories.find("READ MORE"))
        cut = stories.find("&nbsp;</div></td></tr>") + len("&nbsp;</div></td></tr>")
        blocks = stories[:cut] + sponsor_block(CONFIG["sponsor"]) + stories[cut:] if first_end > 0 else stories + sponsor_block(CONFIG["sponsor"])
    body = f'''<!DOCTYPE html>
<html lang="en" xmlns="http://www.w3.org/1999/xhtml">
<head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><meta name="x-apple-disable-message-reformatting">
<title>{esc(subject)}</title>
<style>
  body{{margin:0;padding:0;background:{BG};-webkit-text-size-adjust:100%;}}
  img{{border:0;line-height:100%;outline:none;text-decoration:none;-ms-interpolation-mode:bicubic;}}
  table{{border-collapse:collapse;mso-table-lspace:0;mso-table-rspace:0;}}
  @media only screen and (max-width:620px){{ .wrap{{width:100%!important;}} .h1{{font-size:22px!important;}} }}
</style>
</head>
<body style="margin:0;padding:0;background:{BG};">
<div style="display:none;font-size:1px;line-height:1px;max-height:0;max-width:0;opacity:0;overflow:hidden;mso-hide:all;">{esc(CONFIG["from_name"])} - {edition.upper()} Newsletter &middot; {esc(preheader)}{pad}</div>
<table role="presentation" width="100%" border="0" cellpadding="0" cellspacing="0" style="background:{BG};">
<tr><td align="center" style="padding:18px 8px;">
<table role="presentation" class="wrap" width="600" border="0" cellpadding="0" cellspacing="0" style="width:600px;max-width:600px;background:{PAPER};">

  <tr><td style="padding:8px 25px 0;font-family:{FONT};font-size:11px;color:{GREY};text-align:right;"><a href="{view_url}" target="_blank" style="color:{GREY};text-decoration:underline;">View online</a></td></tr>
  <tr><td align="center" style="padding:14px 25px 12px;"><a href="{SITE}/?utm_source=newsletter&utm_medium=email&utm_campaign={campaign}" target="_blank"><img src="{SITE}/static/img/logo.png" width="360" alt="{esc(CONFIG["from_name"])}" style="display:block;width:360px;max-width:100%;height:auto;"></a></td></tr>
  <tr><td bgcolor="{NAVY}" style="background:{NAVY};padding:10px 25px;font-family:{FONT};font-size:12px;line-height:130%;color:#ffffff;text-align:center;letter-spacing:.08em;text-transform:uppercase;">
    <strong>{esc(CONFIG["tagline"])}</strong><br><span style="color:#c9d1e3;letter-spacing:.04em;">{edition.upper()} Newsletter &middot; {date_et.strftime("%A, %B %-d, %Y")}</span></td></tr>

  {blocks}
  {trending_block(trending, campaign)}

  <tr><td bgcolor="#f1f1f1" style="background:#f1f1f1;padding:22px 25px;font-family:{FONT};font-size:12px;line-height:150%;color:{GREY};text-align:center;">
    <p style="margin:0 0 10px;">{esc(CONFIG["about"])}</p>
    <p style="margin:0 0 10px;">Your email address is on this list as a result of a subscription, information request, or other correspondence you may have had with us. If you would like to be removed from our list, use the following link: <a href="*|UNSUB|*" style="color:{GREY};text-decoration:underline;">Unsubscribe</a>. <a href="{SITE}/privacy-policy/" style="color:{GREY};text-decoration:underline;">Privacy policy</a> &middot; <a href="{view_url}" style="color:{GREY};text-decoration:underline;">View online</a></p>
    <p style="margin:0 0 10px;">{esc(CONFIG["postal_address"])}</p>
    <p style="margin:0;">DISCLAIMER: Content labeled as "AD CONTENT" or "SPONSORED" may be a third-party advertisement and is not endorsed or warranted by {esc(CONFIG["from_name"])}.</p>
  </td></tr>
</table>
</td></tr></table>
</body></html>'''
    text = (f"{CONFIG['from_name']} - {edition.upper()} Newsletter - {date_et.strftime('%A, %B %d, %Y')}\n\n"
            + "\n\n".join(f"{p['title']}\n{link(p, campaign)}" for p in leads)
            + ("\n\nALSO TRENDING\n" + "\n".join(f"- {p['title']}\n  {link(p, campaign)}" for p in trending) if trending else "")
            + f"\n\nUnsubscribe: *|UNSUB|*\n{CONFIG['postal_address']}\n")
    return subject, preheader, body, text


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--edition", choices=["am", "pm"])
    ap.add_argument("--date", help="YYYY-MM-DD (default: today in ET)")
    ap.add_argument("--hours", type=int, default=16, help="lead stories must be published within this many hours")
    ap.add_argument("--token", default=os.environ.get("PUBLISH_TOKEN", ""))
    a = ap.parse_args()
    if not a.token:
        sys.exit("PUBLISH_TOKEN is not set")
    now_et = dt.datetime.now(ET)
    edition = a.edition or ("am" if now_et.hour < 12 else "pm")
    date_et = dt.datetime.strptime(a.date, "%Y-%m-%d").replace(tzinfo=ET) if a.date else now_et
    campaign = f"{date_et.strftime('%Y-%m-%d')}-{edition}"
    view_url = f"{SITE}/static/newsletters/{campaign}.html"
    posts = fetch_stories(a.token)
    leads, trending = pick(posts, a.hours, CONFIG.get("leads", 3), CONFIG.get("trending", 6))
    if not leads:
        sys.exit("no published stories to send")
    subject, preheader, body, text = render(leads, trending, edition, date_et, campaign, view_url)
    OUT.mkdir(parents=True, exist_ok=True)
    STATIC.mkdir(parents=True, exist_ok=True)
    (OUT / f"{campaign}.html").write_text(body, encoding="utf-8")
    (OUT / f"{campaign}.txt").write_text(text, encoding="utf-8")
    (OUT / f"{campaign}.json").write_text(json.dumps({"campaign": campaign, "edition": edition, "subject": subject, "preheader": preheader,
                                                       "leads": [p["slug"] for p in leads], "trending": [p["slug"] for p in trending]}, indent=1))
    shutil.copy(OUT / f"{campaign}.html", STATIC / f"{campaign}.html")
    print(f"{campaign}: subject '{subject}' | preheader '{preheader}'")
    for p in leads:
        print(f"  lead: {p['title']}")
    for p in trending:
        print(f"  trending: {p['title']}")
    print(f"wrote dist/newsletters/{campaign}.html (+ .txt/.json) and src/static/newsletters/{campaign}.html")


if __name__ == "__main__":
    main()
