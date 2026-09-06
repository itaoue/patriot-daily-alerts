"""Import posts and pages from the live WordPress site via its public REST API.

Used by tools/import_wp.py (CLI) and by the /admin/import button, which runs it in a
background thread inside the Railway service so no external database access is needed.
"""
import html
import json
import re
import threading
import time
from datetime import datetime

import requests

from .models import Page, Post, Setting, db, utcnow
from .seed import ensure_categories
from .utils import make_excerpt

UA = {"User-Agent": "Mozilla/5.0 (PatriotDailyAlerts importer)"}
KEEP_PAGES = {
    "about-us": "About Us", "contact-us": "Contact Us", "privacy-policy": "Privacy Policy",
    "terms-of-service": "Terms of Service", "report-spam": "Report Spam",
}
STATUS_KEY = "import_status"
_lock = threading.Lock()


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


def get_status() -> dict:
    row = Setting.query.get(STATUS_KEY)
    return json.loads(row.value) if row else {"state": "idle"}


def set_status(**fields) -> None:
    status = get_status()
    status.update(fields, updated_at=utcnow().isoformat(timespec="seconds"))
    row = Setting.query.get(STATUS_KEY) or Setting(key=STATUS_KEY)
    row.value = json.dumps(status)
    db.session.add(row)
    db.session.commit()


def import_posts(source: str, max_posts: int, log=print) -> int:
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
        log(f"page {page}/{total_pages} — {done} posts")
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


def run_import(app, source: str, max_posts: int) -> None:
    """Full import with progress written to the Setting table (safe to call from a thread)."""
    with app.app_context():
        try:
            set_status(state="running", source=source, max_posts=max_posts, posts=0, pages=0, error="")
            n = import_posts(source, max_posts, log=lambda m: set_status(progress=m))
            set_status(posts=n)
            pages = import_pages(source)
            set_status(state="done", pages=pages, progress=f"Imported {n} posts and {pages} pages.")
        except Exception as exc:  # noqa: BLE001 - surface any failure in the admin UI
            db.session.rollback()
            set_status(state="failed", error=str(exc)[:500])


def start_background_import(app, source: str, max_posts: int) -> bool:
    """Start the import in a daemon thread. Returns False if one is already running."""
    with _lock:
        status = get_status()
        if status.get("state") == "running":
            # A job older than 30 min is presumed dead (worker restart); allow restart.
            stamp = status.get("updated_at")
            if stamp and (utcnow() - datetime.fromisoformat(stamp)).total_seconds() < 1800:
                return False
        set_status(state="running", progress="Starting…", posts=0, pages=0, error="")
    threading.Thread(target=run_import, args=(app, source, max_posts), daemon=True).start()
    return True
