"""Seed the database from tools/seed_data.json (a snapshot of the live WordPress site)."""
import json
import os
from datetime import datetime

from .models import Category, Page, Post, db

SEED_FILE = os.path.join(os.path.dirname(__file__), "..", "tools", "seed_data.json")

# On the WordPress site every post carried "Latest News" (= all posts) plus "Politics",
# so "Latest News" is served by the /latest/ archive and Politics is the real section.
DEFAULT_CATEGORIES = [
    ("politics", "Politics", "Washington, the White House, Congress and the courts."),
]


def ensure_categories() -> dict:
    cats = {}
    for slug, name, desc in DEFAULT_CATEGORIES:
        cat = Category.query.filter_by(slug=slug).first()
        if not cat:
            cat = Category(slug=slug, name=name, description=desc)
            db.session.add(cat)
        cats[slug] = cat
    db.session.flush()
    return cats


def seed_if_empty() -> int:
    cats = ensure_categories()
    if Post.query.count() > 0:
        db.session.commit()
        return 0
    if not os.path.exists(SEED_FILE):
        db.session.commit()
        return 0
    with open(SEED_FILE, encoding="utf-8") as fh:
        data = json.load(fh)
    n = 0
    for p in data["posts"]:
        db.session.add(
            Post(
                wp_id=p.get("wp_id"),
                slug=p["slug"],
                title=p["title"],
                excerpt=p.get("excerpt", ""),
                body_html=p.get("body_html", ""),
                image_url=p.get("image_url", ""),
                author=p.get("author", "Staff"),
                category=cats.get(p.get("category"), cats["politics"]),
                published_at=datetime.fromisoformat(p["published_at"].replace("Z", "+00:00")).replace(tzinfo=None),
                status="published",
            )
        )
        n += 1
    for pg in data.get("pages", []):
        if not Page.query.filter_by(slug=pg["slug"]).first():
            db.session.add(Page(slug=pg["slug"], title=pg["title"], body_html=pg["body_html"]))
    db.session.commit()
    return n
