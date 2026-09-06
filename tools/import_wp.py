"""Import every post and page from the live WordPress site via its public REST API.

Usage (locally, or as a one-off Railway command):
    python tools/import_wp.py                # import everything (2,000+ posts, ~5 min)
    python tools/import_wp.py --max 200      # only the newest 200 posts
    python tools/import_wp.py --source https://patriotdailyalerts.com

Safe to re-run: posts are matched by their WordPress ID and updated in place.
"""
import argparse
import html
import os
import re
import sys
import time
from datetime import datetime

import requests

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.main import app  # noqa: E402
from src.models import Page, Post, db  # noqa: E402
from src.seed import ensure_categories  # noqa: E402
from src.utils import make_excerpt  # noqa: E402

UA = {"User-Agent": "Mozilla/5.0 (PatriotDailyAlerts importer)"}
KEEP_PAGES = {
    "about-us": "About Us", "contact-us": "Contact Us", "privacy-policy": "Privacy Policy",
    "terms-of-service": "Terms of Service", "report-spam": "Report Spam",
}


def clean_html(h: str) -> str:
    h = re.sub(r"<!--.*?-->", "", h or "", flags=re.S)
    h = re.sub(r"<script.*?</script>", "", h, flags=re.S)
    return h.strip()


def fetch(url, params):
    for attempt in range(4):
        r = requests.get(url, params=params, headers=UA, timeout=40)
        if r.status_code == 200:
            return r
        if r.status_code == 400:  # past the last page
            return None
        time.sleep(2 * (attempt + 1))
    r.raise_for_status()


def import_posts(source: str, max_posts: int) -> int:
    cats = ensure_categories()
    page, done = 1, 0
    while True:
        r = fetch(f"{source}/wp-json/wp/v2/posts", {"per_page": 50, "page": page, "_embed": 1})
        if r is None:
            break
        items = r.json()
        if not items:
            break
        for p in items:
            media = (p.get("_embedded", {}).get("wp:featuredmedia") or [{}])[0]
            terms = p.get("_embedded", {}).get("wp:term") or [[]]
            slugs = [t["slug"] for t in terms[0]]
            body = clean_html(p["content"]["rendered"])
            excerpt = re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", "", p["excerpt"]["rendered"]))).replace("[…]", "").strip()
            post = Post.query.filter_by(wp_id=p["id"]).first() or Post.query.filter_by(slug=p["slug"]).first() or Post()
            post.wp_id = p["id"]
            post.slug = p["slug"]
            post.title = html.unescape(p["title"]["rendered"])
            post.excerpt = excerpt or make_excerpt(body)
            post.body_html = body
            post.image_url = media.get("source_url", "") or ""
            post.category = cats.get(next((s for s in slugs if s in cats and s != "latest-news"), "politics"), cats["politics"])
            post.status = "published" if p.get("status") == "publish" else "draft"
            post.published_at = datetime.fromisoformat(p["date_gmt"])
            db.session.add(post)
            done += 1
            if max_posts and done >= max_posts:
                db.session.commit()
                return done
        db.session.commit()
        total_pages = int(r.headers.get("X-WP-TotalPages", page))
        print(f"  page {page}/{total_pages} — {done} posts", flush=True)
        if page >= total_pages:
            break
        page += 1
    return done


def import_pages(source: str) -> int:
    r = fetch(f"{source}/wp-json/wp/v2/pages", {"per_page": 50})
    n = 0
    for pg in r.json() if r else []:
        if pg["slug"] not in KEEP_PAGES:
            continue
        page = Page.query.filter_by(slug=pg["slug"]).first() or Page(slug=pg["slug"])
        page.title = KEEP_PAGES[pg["slug"]]
        page.body_html = clean_html(pg["content"]["rendered"])
        db.session.add(page)
        n += 1
    db.session.commit()
    return n


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--source", default="https://patriotdailyalerts.com")
    ap.add_argument("--max", type=int, default=0, help="stop after N newest posts (0 = all)")
    ap.add_argument("--skip-pages", action="store_true")
    args = ap.parse_args()
    with app.app_context():
        print(f"Importing from {args.source} …")
        n = import_posts(args.source.rstrip("/"), args.max)
        print(f"Imported/updated {n} posts")
        if not args.skip_pages:
            print(f"Imported/updated {import_pages(args.source.rstrip('/'))} pages")
