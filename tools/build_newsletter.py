#!/usr/bin/env python3
"""
Build the Patriot Daily Alerts email newsletter (AM / PM edition) from the site's published stories.

    python tools/build_newsletter.py                 # one daily issue: <date>-daily
    python tools/build_newsletter.py --edition pm --date 2026-09-08   # am/pm ids if you ever run two a day
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


POLL_SCHEMA = {"type": "object", "additionalProperties": False, "required": ["question", "options"],
               "properties": {"question": {"type": "string"}, "options": {"type": "array", "items": {"type": "string"}}}}  # 2-3 options enforced in code


def poll_question(leads, campaign):
    """One reader-poll question about the LEAD story: specific to its decision/claim/event, never a generic approval question."""
    cfg = CONFIG.get("poll") or {}
    lead = leads[0]
    import re as _re

    body = _re.sub(r"<[^>]+>", " ", lead.get("body_html") or "")
    body = _re.sub(r"\s+", " ", html.unescape(body)).strip()[:1500]
    if os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN"):
        try:
            import anthropic

            client = anthropic.Anthropic(max_retries=3)
            prompt = (
                "Write ONE reader-poll question for a conservative American news email, about the specific story below.\n\n"
                "Rules:\n"
                "- The question must be about the concrete decision, claim, or event in THIS story: name the person or institution and the action.\n"
                "- Ask the reader's opinion or prediction about that specific thing. Under 18 words. Plain, direct, no jargon.\n"
                "- Never ask a generic question. BAD: 'Do you approve of the job President Trump is doing?' or 'Is the media telling the truth?'\n"
                "  GOOD: 'Should the Supreme Court let the Postal Service enforce its mail-ballot rule before the midterms?' / "
                "'Was Giuliani right to tell Mamdani to skip the 9/11 ceremony?' / 'Will Bessent's $40 oil prediction come true this year?'\n"
                "- 2 or 3 answer options tailored to the question, each under 6 words (e.g. 'Yes, enforce it' / 'No, wait for the courts' / 'Not sure').\n"
                "- Do not name private individuals.\n\n"
                f"Headline: {lead['title']}\nSummary: {lead.get('excerpt', '')}\nStory: {body}"
            )
            msg = client.messages.create(
                model=os.environ.get("EDITOR_MODEL", "claude-sonnet-5"), max_tokens=400,
                output_config={"effort": "low", "format": {"type": "json_schema", "schema": POLL_SCHEMA}},
                messages=[{"role": "user", "content": prompt}])
            if msg.stop_reason == "end_turn":
                data = json.loads(next(b.text for b in msg.content if b.type == "text"))
                if data.get("question") and len(data.get("options", [])) >= 2:
                    return data["question"][:300], [o[:120] for o in data["options"][:3]]
        except Exception as exc:  # noqa: BLE001 - never block the issue on the poll
            print(f"poll question generation failed ({exc}); using story-based fallback")
    if not cfg.get("fallback", True):
        return None, None
    # fallback without Claude: still about the lead story rather than a generic question
    return f"{lead['title']} — right call or wrong call?"[:300], ["Right call", "Wrong call", "Not sure"]


SUBJECT_SCHEMA = {"type": "object", "additionalProperties": False, "required": ["subject", "teaser"],
                  "properties": {"subject": {"type": "string"}, "teaser": {"type": "string"}}}
CHECK_SCHEMA = {"type": "object", "additionalProperties": False, "required": ["subject_ok", "teaser_ok", "subject", "teaser", "notes"],
                "properties": {"subject_ok": {"type": "boolean"}, "teaser_ok": {"type": "boolean"}, "subject": {"type": "string"},
                               "teaser": {"type": "string"}, "notes": {"type": "string"}}}


def fact_check_lines(client, subject, teaser, stories_text):
    """Second pass: every word of the subject/teaser must be supported by the stories; overstated verbs get rewritten."""
    msg = client.messages.create(
        model=os.environ.get("EDITOR_MODEL", "claude-sonnet-5"), max_tokens=400,
        output_config={"effort": "low", "format": {"type": "json_schema", "schema": CHECK_SCHEMA}},
        messages=[{"role": "user", "content": (
            "You are a fact-checker for email subject lines. For each line, decide whether EVERY claim and verb is supported by the story text "
            "(a proposal or comment period is not a decision; 'blocks/bans/fires/kills/wins' require that outcome to have happened; numbers and "
            "names must appear in the story). If a line overstates, rewrite it minimally in the same style (keep exactly one ALL-CAPS word, "
            "the em dash, 55-90 characters) so it is accurate. Return subject_ok/teaser_ok and the final lines.\n\n"
            f"SUBJECT: {subject}\nTEASER: {teaser}\n\n{stories_text}")}])
    if msg.stop_reason != "end_turn":
        return subject, teaser
    data = json.loads(next(b.text for b in msg.content if b.type == "text"))
    if not data.get("subject_ok") or not data.get("teaser_ok"):
        print(f"  subject check: {data.get('notes', '')[:200]}")
    return (data.get("subject") or subject).strip(), (data.get("teaser") or teaser).strip()

SUBJECT_RULES = (
    "You write email subject lines for a conservative American news daily, in the style of Middle America News.\n"
    "Formula: [the specific event, naming the person or institution] then a dash or colon and [the consequence, reaction, or withheld detail]. "
    "8 to 13 words, 55 to 90 characters. Present tense, strong active verbs (Draws the Line, Steps Off, Drops, Blocks, Erupts). "
    "Exactly ONE word in ALL CAPS, placed on the key verb or adjective (choose from: BREAKING, BOMBSHELL, EXPOSED, BLOCKS, FIRED, SILENT, "
    "MASSIVE, SHOCKING, DEVASTATING, URGENT, STUNNING, REFUSES, WARNS). Name the who; hold back one concrete detail to create curiosity "
    "(e.g. 'Names Two Americans', 'His Own Minister Refuses', 'Wait Until You Hear Why'). Heroes and villains are fine when the story supports them. "
    "Hard rules: every claim must be true to the story text below - never promise something the story does not contain; the verb must match what "
    "actually happened (a proposal, a comment period, a lawsuit filed or a report is NOT a decision: never say BLOCKS, BANS, FIRES, KILLS or WINS "
    "unless the story says that outcome occurred); no question marks; "
    "no numbers unless they are in the story; no emojis; no quotation marks; do not start with the ALL-CAPS word more than one time in three.\n"
    "Examples of the target style: 'Trump Drops MASSIVE Cash Bomb Into Red State Senate Race - Democrats in Desperation Mode' / "
    "'Minnesota Court BLOCKS Patriot's Demand for Vote Recount After Disturbing Irregularities Surface' / "
    "'Netanyahu Orders Settler Outposts Torn Down - His Own Minister REFUSES'.\n"
    "Return JSON: subject = the subject line for STORY 1; teaser = a line in the same style for STORY 2 (it is shown after the word 'and' as inbox preview text, so do not start it with 'and')."
)


def email_subject(leads, campaign):
    """Middle America News-style subject (lead) and preheader teaser (second story); falls back to the story titles."""
    fallback = (leads[0]["title"], leads[1]["title"] if len(leads) > 1 else CONFIG["tagline"])
    if not (os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN")):
        return fallback
    import re as _re

    def story(p, n):
        body = _re.sub(r"<[^>]+>", " ", p.get("body_html") or "")
        body = _re.sub(r"\s+", " ", html.unescape(body)).strip()[:1800]
        return f"STORY {n}\nHeadline: {p['title']}\nSummary: {p.get('excerpt', '')}\nText: {body}"

    def has_caps(t):
        return bool(_re.search(r"\b[A-Z]{4,}\b", t))

    def tidy(t):
        return _re.sub(r"\s+[-–]\s+", " — ", t.strip().strip('"'))  # Middle America News uses the em dash

    try:
        import anthropic

        client = anthropic.Anthropic(max_retries=3)
        content = story(leads[0], 1) + ("\n\n" + story(leads[1], 2) if len(leads) > 1 else "")
        messages = [{"role": "user", "content": content}]
        for attempt in range(2):
            msg = client.messages.create(
                model=os.environ.get("EDITOR_MODEL", "claude-sonnet-5"), max_tokens=300,
                output_config={"effort": "low", "format": {"type": "json_schema", "schema": SUBJECT_SCHEMA}},
                system=SUBJECT_RULES, messages=messages)
            if msg.stop_reason != "end_turn":
                break
            data = json.loads(next(b.text for b in msg.content if b.type == "text"))
            subject, teaser = tidy(data.get("subject", "")), tidy(data.get("teaser", ""))
            if 40 <= len(subject) <= 110 and has_caps(subject) and (len(teaser) < 20 or has_caps(teaser)):
                subject, teaser = fact_check_lines(client, subject, teaser, content)
                subject, teaser = tidy(subject), tidy(teaser)
                return subject[:120], (teaser[:120] if len(teaser) >= 20 else fallback[1])
            if attempt == 0:  # one corrective pass: the ALL-CAPS anchor word is mandatory in both lines
                messages += [{"role": "assistant", "content": json.dumps(data)},
                             {"role": "user", "content": "Rewrite: the subject AND the teaser must each contain exactly one ALL-CAPS anchor word "
                                                         "(e.g. BOMBSHELL, BLOCKS, REFUSES, EXPOSED, STUNNING, WARNS) and stay within 55-90 characters. Same facts."}]
        if 40 <= len(subject) <= 110:
            return subject[:120], (teaser[:120] if len(teaser) >= 20 else fallback[1])
    except Exception as exc:  # noqa: BLE001
        print(f"subject line generation failed ({exc}); using story titles")
    return fallback


def create_poll(token, campaign, question, options, refresh=False):
    r = requests.post(f"{SITE}/api/polls", json={"campaign": campaign, "question": question, "options": options, "update": refresh},
                      headers={"Authorization": f"Bearer {token}", "User-Agent": "Mozilla/5.0"}, timeout=30)
    r.raise_for_status()
    return r.json()


def poll_block(poll):
    if not poll:
        return ""
    heading = (CONFIG.get("poll") or {}).get("heading", "Today's Poll")
    buttons = "".join(
        f'<tr><td align="center" style="padding:5px 25px;"><table role="presentation" width="100%" border="0" cellpadding="0" cellspacing="0"><tr>'
        f'<td align="center" bgcolor="{PAPER}" style="border:2px solid {NAVY};border-radius:3px;padding:11px 14px;">'
        f'<a href="{esc(u)}?utm_source=newsletter&utm_medium=email&utm_campaign=poll" target="_blank" style="display:block;font-family:{FONT};font-size:17px;font-weight:700;color:{NAVY};text-decoration:none;">{esc(label)}</a>'
        f'</td></tr></table></td></tr>'
        for label, u in zip(poll["options"], poll["vote_urls"]))
    return f'''
<tr><td style="padding:22px 25px 4px;font-family:{FONT};font-size:20px;line-height:120%;font-weight:700;color:{INK};text-transform:uppercase;letter-spacing:.04em;text-align:center;">
  <span style="border-bottom:3px solid {RED};padding-bottom:4px;">{esc(heading)}</span></td></tr>
<tr><td style="padding:14px 25px 8px;font-family:{FONT};font-size:22px;line-height:125%;font-weight:700;color:{INK};text-align:center;">{esc(poll["question"])}</td></tr>
{buttons}
<tr><td style="padding:8px 25px 22px;font-family:{FONT};font-size:12px;color:{GREY};text-align:center;">Tap an answer to vote. <a href="{esc(poll["results_url"])}" target="_blank" style="color:{GREY};text-decoration:underline;">See the results so far</a>.</td></tr>'''


def render(leads, trending, edition, date_et, campaign, view_url, poll=None, subject_line=None):
    subject, teaser = subject_line or (leads[0]["title"], leads[1]["title"] if len(leads) > 1 else CONFIG["tagline"])
    preheader = f"and {teaser}" if len(leads) > 1 else CONFIG["tagline"]
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
<div style="display:none;font-size:1px;line-height:1px;max-height:0;max-width:0;opacity:0;overflow:hidden;mso-hide:all;">{esc(preheader)}{pad}</div>
<table role="presentation" width="100%" border="0" cellpadding="0" cellspacing="0" style="background:{BG};">
<tr><td align="center" style="padding:18px 8px;">
<table role="presentation" class="wrap" width="600" border="0" cellpadding="0" cellspacing="0" style="width:600px;max-width:600px;background:{PAPER};">

  <tr><td style="padding:8px 25px 0;font-family:{FONT};font-size:11px;color:{GREY};text-align:right;"><a href="{view_url}" target="_blank" style="color:{GREY};text-decoration:underline;">View online</a></td></tr>
  <tr><td align="center" style="padding:14px 25px 12px;"><a href="{SITE}/?utm_source=newsletter&utm_medium=email&utm_campaign={campaign}" target="_blank"><img src="{SITE}/static/img/logo.png" width="360" alt="{esc(CONFIG["from_name"])}" style="display:block;width:360px;max-width:100%;height:auto;"></a></td></tr>
  <tr><td bgcolor="{NAVY}" style="background:{NAVY};padding:10px 25px;font-family:{FONT};font-size:12px;line-height:130%;color:#ffffff;text-align:center;letter-spacing:.08em;text-transform:uppercase;">
    <strong>{esc(CONFIG["tagline"])}</strong><br><span style="color:#c9d1e3;letter-spacing:.04em;">{date_et.strftime("%A, %B %-d, %Y")}</span></td></tr>

  {blocks}
  {trending_block(trending, campaign)}
  {poll_block(poll)}

  <tr><td bgcolor="#f1f1f1" style="background:#f1f1f1;padding:22px 25px;font-family:{FONT};font-size:12px;line-height:150%;color:{GREY};text-align:center;">
    <p style="margin:0 0 10px;">{esc(CONFIG["about"])}</p>
    <p style="margin:0 0 10px;">Your email address is on this list as a result of a subscription, information request, or other correspondence you may have had with us. If you would like to be removed from our list, use the following link: <a href="*|UNSUB|*" style="color:{GREY};text-decoration:underline;">Unsubscribe</a>. <a href="{SITE}/privacy-policy/" style="color:{GREY};text-decoration:underline;">Privacy policy</a> &middot; <a href="{view_url}" style="color:{GREY};text-decoration:underline;">View online</a></p>
    <p style="margin:0 0 10px;">{esc(CONFIG["postal_address"])}</p>
    <p style="margin:0;">DISCLAIMER: Content labeled as "AD CONTENT" or "SPONSORED" may be a third-party advertisement and is not endorsed or warranted by {esc(CONFIG["from_name"])}.</p>
  </td></tr>
</table>
</td></tr></table>
</body></html>'''
    text = (f"{CONFIG['from_name']} - {date_et.strftime('%A, %B %d, %Y')}\n\n"
            + "\n\n".join(f"{p['title']}\n{link(p, campaign)}" for p in leads)
            + ("\n\nALSO TRENDING\n" + "\n".join(f"- {p['title']}\n  {link(p, campaign)}" for p in trending) if trending else "")
            + (f"\n\nTODAY'S POLL: {poll['question']}\n" + "\n".join(f"- {o}: {u}" for o, u in zip(poll["options"], poll["vote_urls"])) if poll else "")
            + f"\n\nUnsubscribe: *|UNSUB|*\n{CONFIG['postal_address']}\n")
    return subject, preheader, body, text


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--edition", choices=["daily", "am", "pm"], default="daily")
    ap.add_argument("--date", help="YYYY-MM-DD (default: today in ET)")
    ap.add_argument("--hours", type=int, default=16, help="lead stories must be published within this many hours")
    ap.add_argument("--token", default=os.environ.get("PUBLISH_TOKEN", ""))
    ap.add_argument("--skip-if-exists", action="store_true", help="do nothing if this issue was already built (scheduled runs)")
    ap.add_argument("--lead", help="slug of the story to run as the lead (top of the issue and subject line)")
    ap.add_argument("--subject", help="use this subject line instead of generating one")
    ap.add_argument("--teaser", help="use this preheader teaser (shown after 'and') instead of generating one")
    ap.add_argument("--refresh-poll", action="store_true", help="replace this issue's poll question (e.g. after changing the lead)")
    ap.add_argument("--since", help="UTC time YYYY-MM-DDTHH:MM:SS; with --min-new, skip unless that many stories were published after it")
    ap.add_argument("--min-new", type=int, default=0)
    a = ap.parse_args()
    if not a.token:
        sys.exit("PUBLISH_TOKEN is not set")
    now_et = dt.datetime.now(ET)
    edition = a.edition
    date_et = dt.datetime.strptime(a.date, "%Y-%m-%d").replace(tzinfo=ET) if a.date else now_et
    campaign = f"{date_et.strftime('%Y-%m-%d')}-{edition}"
    view_url = f"{SITE}/static/newsletters/{campaign}.html"
    if a.skip_if_exists and (STATIC / f"{campaign}.html").exists():
        print(f"{campaign}: already built earlier today, skipping")
        return
    posts = fetch_stories(a.token)
    if a.since and a.min_new:
        fresh = [p for p in posts if p["published_at"] >= a.since]
        if len(fresh) < a.min_new:
            print(f"{campaign}: not enough new stories since {a.since} ({len(fresh)} < {a.min_new}), skipping")
            return
    leads, trending = pick(posts, a.hours, CONFIG.get("leads", 3), CONFIG.get("trending", 6))
    if a.lead:
        chosen = next((p for p in posts if p["slug"] == a.lead), None)
        if not chosen:
            sys.exit(f"--lead {a.lead}: no published story with that slug in the last days")
        leads = [chosen] + [p for p in leads if p["slug"] != a.lead]
        leads = leads[: CONFIG.get("leads", 3)]
        trending = [p for p in posts if p not in leads][: CONFIG.get("trending", 6)]
    if not leads:
        sys.exit("no published stories to send")
    poll = None
    if (CONFIG.get("poll") or {}).get("enabled", True):
        question, options = poll_question(leads, campaign)
        if question:
            try:
                poll = create_poll(a.token, campaign, question, options, refresh=a.refresh_poll)
                print(f"  poll: {poll['question']} ({' / '.join(poll['options'])})")
            except requests.RequestException as exc:
                print(f"  poll skipped: {exc}")
    subject_line = email_subject(leads, campaign)
    if a.subject or a.teaser:
        subject_line = (a.subject or subject_line[0], a.teaser or subject_line[1])
    print(f"  subject: {subject_line[0]}\n  teaser:  and {subject_line[1]}")
    subject, preheader, body, text = render(leads, trending, edition, date_et, campaign, view_url, poll, subject_line)
    OUT.mkdir(parents=True, exist_ok=True)
    STATIC.mkdir(parents=True, exist_ok=True)
    (OUT / f"{campaign}.html").write_text(body, encoding="utf-8")
    (OUT / f"{campaign}.txt").write_text(text, encoding="utf-8")
    (OUT / f"{campaign}.json").write_text(json.dumps({"campaign": campaign, "edition": edition, "subject": subject, "preheader": preheader,
                                                       "leads": [p["slug"] for p in leads], "trending": [p["slug"] for p in trending],
                                                       "poll": poll and {"id": poll["id"], "question": poll["question"]}}, indent=1))
    shutil.copy(OUT / f"{campaign}.html", STATIC / f"{campaign}.html")
    print(f"{campaign}: subject '{subject}' | preheader '{preheader}'")
    for p in leads:
        print(f"  lead: {p['title']}")
    for p in trending:
        print(f"  trending: {p['title']}")
    print(f"wrote dist/newsletters/{campaign}.html (+ .txt/.json) and src/static/newsletters/{campaign}.html")


if __name__ == "__main__":
    main()
