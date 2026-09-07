#!/usr/bin/env python3
"""
Write original stories from the topic radar with Claude, then post them to the site as drafts.

    python tools/write_stories.py                    # 3 stories from the newest radar file
    python tools/write_stories.py --count 2
    python tools/write_stories.py --pick 4,9         # force radar ranks
    python tools/write_stories.py --dry-run          # show the picks, call nothing
    python tools/write_stories.py --status published # publish immediately (default: draft)

For every pick: (1) Claude reads two or three outlet stories and the primary source behind them
(web search + web fetch server tools) and returns research notes; (2) Claude writes an original
story as JSON in the house style; (3) a second Claude pass acts as editor and checks every claim
against the notes; (4) a photo is attached (licensed Wikimedia photo of the public figure, else an
xAI-generated editorial image) and saved under src/static/uploads/yyyy/mm/ for git; (5) the story
is POSTed to /api/publish. Picks go to content/radar/used.json so a story is never written twice.

Environment: ANTHROPIC_API_KEY, XAI_API_KEY, PUBLISH_TOKEN, SITE_URL (default https://patriotdailyalerts.com).
"""
import argparse
import datetime as dt
import json
import os
import pathlib
import re
import sys
import traceback

import requests

ROOT = pathlib.Path(__file__).resolve().parent.parent
RADAR = ROOT / "content" / "radar"
USED = RADAR / "used.json"
UPLOADS = ROOT / "src" / "static" / "uploads"
sys.path.insert(0, str(ROOT / "tools"))

MODEL = "claude-opus-5"
CATEGORIES = ["politics", "culture", "economy", "world", "border"]
SITE = os.environ.get("SITE_URL", "https://patriotdailyalerts.com").rstrip("/")

SYSTEM = """You write news stories for Patriot Daily Alerts, a conservative American news site read by patriotic, mostly older Americans who distrust the legacy media. Voice: confident, plain-spoken, concrete, a little wry; the perspective is conservative but the reporting is straight. Every story is ORIGINAL WRITING built from the facts in the research notes. Never copy or closely paraphrase another outlet's sentences; do not reproduce more than two short quotes from any one source; quote public figures only in the exact words the notes give. Never invent facts, numbers, quotes or names. If the notes flag something as unconfirmed, say so in the story. Do not write about private individuals; do not accuse anyone of a crime unless a court or law enforcement has done so. No advice, no calls to action, no "share this", no exclamation points, no cliches ("bombshell", "slams", "destroys"). Attribute reporting naturally in the body, once per outlet ("Newsmax reported", "according to the Washington Examiner") and cite the primary source when there is one (the court order, the agency statement, the post on X).

Format: return the body as clean HTML using only <p>, <h2>, <blockquote>, <strong>, <em>, <a href> tags. Open with a two- or three-paragraph lede that states the news and why it matters; then two to four <h2> sections with specific headings; close with a short paragraph on what happens next. 500 to 800 words. Links in the body only to primary sources and the outlets you attribute. No "Sources" section in the body (sources are a separate field). Title style, like the site's own: specific, active, one hook, no colon-and-cliche, e.g. "Vance Walks Into the Toughest Room in His Party", "One Barcode Fails, and Ten Thousand Ballots Go Back", "Congress Is Back, and the Clock Is Already Short". Excerpt: one or two sentences that stand alone in an email."""

SCHEMA = {
    "type": "object", "additionalProperties": False,
    "required": ["title", "slug", "excerpt", "category", "body_html", "sources", "person", "person_query", "image_scene"],
    "properties": {
        "title": {"type": "string"},
        "slug": {"type": "string", "description": "lowercase, hyphenated, 4 to 8 words, no dates"},
        "excerpt": {"type": "string"},
        "category": {"type": "string", "enum": CATEGORIES},
        "body_html": {"type": "string"},
        "sources": {"type": "array", "description": "2 to 6 sources: primary source first if any, then the outlets used",
                    "items": {"type": "object", "additionalProperties": False, "required": ["label", "url"],
                              "properties": {"label": {"type": "string"}, "url": {"type": "string"}}}},
        "person": {"type": "string", "description": "the public figure the story centres on, or an empty string"},
        "person_query": {"type": "string", "description": "Wikimedia Commons search query for a photo of that person (e.g. 'JD Vance 2025 official'), or empty"},
        "image_scene": {"type": "string", "description": "one sentence describing a photorealistic editorial scene for the header image (no text, no logos, no famous people, no faces)"},
    },
}
EDITOR_SCHEMA = {
    "type": "object", "additionalProperties": False,
    "required": ["verdict", "notes", "issues"],
    "properties": {
        "verdict": {"type": "string", "enum": ["approve", "revise", "reject"]},
        "notes": {"type": "string", "description": "two to five sentences for the human editor"},
        "issues": {"type": "array", "items": {"type": "string"}},
    },
}


def load_used():
    return json.loads(USED.read_text()) if USED.exists() else {"urls": [], "titles": []}


def norm(t):
    return " ".join(sorted(w for w in re.findall(r"[a-z0-9]+", t.lower()) if len(w) > 3))


def choose(clusters, count, used, site_recent):
    seen = {norm(t) for t in used["titles"]} | {norm(p["title"]) for p in site_recent}
    used_urls = set(used["urls"])
    fresh = [c for c in clusters if not c["written"] and norm(c["title"]) not in seen
             and not any(o["url"] in used_urls for o in c["outlets"])]
    picks, cats = [], set()
    for c in fresh:  # radar order = best first; spread across sections
        if len(picks) >= count:
            break
        if c["category"] in cats:
            continue
        picks.append(c)
        cats.add(c["category"])
    for c in fresh:
        if len(picks) >= count:
            break
        if c not in picks:
            picks.append(c)
    return picks[:count]


def stream_text(client, **kwargs):
    with client.messages.stream(**kwargs) as stream:
        msg = stream.get_final_message()
    return msg


def research(client, c):
    tools = [{"type": "web_search_20260209", "name": "web_search", "max_uses": 6},
             {"type": "web_fetch_20260209", "name": "web_fetch", "max_uses": 6, "max_content_tokens": 30000}]
    outlets = "\n".join(f"- {o['name']}: {o['title']} <{o['url']}>" for o in c["outlets"][:5])
    ask = (f"Research this developing story for a news write-up. Do not write the story yet.\n\nWorking headline: {c['title']}\n"
           f"Outlets covering it (links may be Google News redirects; if so, search the headline with the outlet name and fetch the outlet's page):\n{outlets}\n\n"
           "Read two or three of these stories, then look for the PRIMARY source behind them (court filing, official statement or press release, "
           "the post on X, transcript, government data) and read it if you can.\n"
           "Return plain-text notes: (1) for each source read: canonical URL, publisher, author, date; (2) the news in two sentences; "
           "(3) every specific fact, number, date, name and named source, one per line, each tagged with which source it came from; "
           "(4) up to four short direct quotes, verbatim, with who said them and where; (5) what is disputed, unconfirmed, or only claimed by one outlet; "
           "(6) the timeline and what happens next.")
    messages = [{"role": "user", "content": ask}]
    for _ in range(5):
        msg = stream_text(client, model=MODEL, max_tokens=16000, tools=tools, messages=messages)
        messages.append({"role": "assistant", "content": msg.content})
        if msg.stop_reason != "pause_turn":
            break
    if msg.stop_reason == "refusal":
        raise RuntimeError("research declined by the model")
    return "\n".join(b.text for b in msg.content if b.type == "text")


def write(client, c, notes, date):
    user = (f"Research notes:\n\n{notes}\n\n---\nRadar hints: category={c['category']}; people={', '.join(c['people']) or 'none'}; "
            f"outlets covering it: {', '.join(o['name'] for o in c['outlets'])}.\nToday is {date}.\n\n"
            "Write the story now as JSON. Choose the category from: " + ", ".join(CATEGORIES) + ".")
    msg = stream_text(client, model=MODEL, max_tokens=20000,
                      system=[{"type": "text", "text": SYSTEM, "cache_control": {"type": "ephemeral"}}],
                      messages=[{"role": "user", "content": user}],
                      output_config={"format": {"type": "json_schema", "schema": SCHEMA}})
    if msg.stop_reason == "refusal":
        raise RuntimeError(f"model declined to write: {getattr(msg, 'stop_details', None)}")
    if msg.stop_reason == "max_tokens":
        raise RuntimeError("story JSON was cut off at max_tokens")
    art = json.loads(next(b.text for b in msg.content if b.type == "text"))
    art["sources"] = [s for s in art.get("sources", []) if s.get("url")][:6] or [{"label": o["name"], "url": o["url"]} for o in c["outlets"][:2]]
    if art["category"] not in CATEGORIES:
        art["category"] = c["category"]
    return art


def edit(client, art, notes):
    user = ("You are the editor. Check this draft against the research notes and answer as JSON.\n\n"
            "Checks: every fact, number, name and quote in the draft must be traceable to the notes (quotes verbatim); nothing presented as fact that the notes "
            "call unconfirmed; no private individuals named; no accusation of a crime without a court or law-enforcement basis; no sentence that reads as "
            "copied from an outlet; the title matches the story; the story stays within the outlets' and primary source's actual claims; tone is news, not a rant. "
            "verdict: approve if it can run as is, revise if a human should fix something specific, reject if the story should not run.\n\n"
            f"=== NOTES ===\n{notes}\n\n=== DRAFT ===\nTitle: {art['title']}\nExcerpt: {art['excerpt']}\n\n{art['body_html']}")
    msg = stream_text(client, model=MODEL, max_tokens=4000, messages=[{"role": "user", "content": user}],
                      output_config={"format": {"type": "json_schema", "schema": EDITOR_SCHEMA}})
    if msg.stop_reason != "end_turn":
        return {"verdict": "revise", "notes": f"editor pass did not complete ({msg.stop_reason})", "issues": []}
    return json.loads(next(b.text for b in msg.content if b.type == "text"))


def attach_image(art, slug, date, no_image):
    if no_image:
        return "", ""
    ym = pathlib.Path(date[:4]) / date[5:7]
    out = UPLOADS / ym / f"{slug}.jpg"
    url = f"{SITE}/wp-content/uploads/{ym.as_posix()}/{slug}.jpg"
    if art.get("person") and art.get("person_query"):
        try:
            from fetch_person_photo import fetch
            credit = fetch(art["person_query"], out)
            if credit:
                return url, f"Photo: {credit}"
            print("  no licensed photo found, generating instead")
        except Exception as exc:  # noqa: BLE001
            print(f"  Wikimedia lookup failed ({exc}); generating instead")
    try:
        from make_story_art import generate
        generate(art["image_scene"], out)
        return url, "Photo: generated editorial image"
    except Exception as exc:  # noqa: BLE001
        print(f"  image generation failed: {exc}")
        return "", ""


def publish(art, status, notes, image_url, token, site_recent_slugs):
    slug = re.sub(r"[^a-z0-9-]", "", art["slug"].lower().replace(" ", "-")).strip("-")[:80] or "story"
    payload = {"title": art["title"], "slug": slug, "excerpt": art["excerpt"], "category": art["category"], "body_html": art["body_html"],
               "sources": art["sources"], "image_url": image_url, "status": status, "editor_notes": notes, "author": os.environ.get("STORY_AUTHOR", "Staff")}
    r = requests.post(f"{SITE}/api/publish", json=payload, headers={"Authorization": f"Bearer {token}"}, timeout=60)
    if r.status_code == 409:
        payload["slug"] = f"{slug}-{dt.date.today().strftime('%m%d')}"
        r = requests.post(f"{SITE}/api/publish", json=payload, headers={"Authorization": f"Bearer {token}"}, timeout=60)
    r.raise_for_status()
    return r.json()


def site_recent(token):
    try:
        r = requests.get(f"{SITE}/api/posts/recent?days=14", headers={"Authorization": f"Bearer {token}"}, timeout=30)
        return r.json().get("posts", []) if r.ok else []
    except requests.RequestException:
        return []


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--count", type=int, default=3)
    ap.add_argument("--radar", help="radar json (default: newest in content/radar)")
    ap.add_argument("--pick", help="comma-separated radar ranks to force")
    ap.add_argument("--status", choices=["draft", "published"], default="draft")
    ap.add_argument("--date", default=dt.date.today().isoformat())
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--no-image", action="store_true")
    a = ap.parse_args()
    token = os.environ.get("PUBLISH_TOKEN", "")
    radar = pathlib.Path(a.radar) if a.radar else sorted(RADAR.glob("[0-9]*.json"))[-1]
    clusters = json.loads(radar.read_text())["clusters"]
    used = load_used()
    recent = site_recent(token) if token else []
    picks = [c for c in clusters if str(c["rank"]) in a.pick.split(",")] if a.pick else choose(clusters, a.count, used, recent)
    print(f"radar {radar.name}: {len(clusters)} clusters; picking {len(picks)} (site has {len(recent)} recent stories)")
    for c in picks:
        print(f"  [{c['rank']}] {c['score']} {c['category']:<8} {c['title']}  ({', '.join(o['name'] for o in c['outlets'][:4])})")
    if a.dry_run:
        return
    if not token:
        sys.exit("PUBLISH_TOKEN is not set")
    import anthropic
    client = anthropic.Anthropic()
    made = []
    for c in picks:
        print(f"\n== {c['title'][:90]}")
        try:
            notes = research(client, c)
            if len(notes) < 400:
                print("  research came back thin, skipping")
                continue
            art = write(client, c, notes, a.date)
            verdict = edit(client, art, notes)
            print(f"  wrote '{art['title']}' [{art['category']}] editor={verdict['verdict']}")
            slug = re.sub(r"[^a-z0-9-]", "", art["slug"].lower().replace(" ", "-")).strip("-")[:80]
            image_url, credit = attach_image(art, slug, a.date, a.no_image)
            notes_out = f"Editor verdict: {verdict['verdict'].upper()}. {verdict['notes']}"
            if verdict.get("issues"):
                notes_out += "\nIssues:\n- " + "\n- ".join(verdict["issues"])
            if credit:
                notes_out += f"\n{credit}"
            notes_out += f"\nRadar: rank {c['rank']}, score {c['score']}, outlets: {', '.join(o['name'] for o in c['outlets'])}"
            status = a.status if verdict["verdict"] == "approve" else "draft"
            res = publish(art, status, notes_out, image_url, token, {p["slug"] for p in recent})
            print(f"  posted as {res['status']}: {SITE}{res['admin_url']}")
            used["urls"] += [o["url"] for o in c["outlets"]]
            used["titles"].append(c["title"])
            made.append(res["slug"])
        except Exception as exc:  # noqa: BLE001
            traceback.print_exc()
            print(f"  FAILED: {exc.__class__.__name__}: {str(exc)[:300]}")
    USED.write_text(json.dumps(used, indent=1))
    print(f"\ndone: {len(made)} story(ies): {', '.join(made)}")


if __name__ == "__main__":
    main()
