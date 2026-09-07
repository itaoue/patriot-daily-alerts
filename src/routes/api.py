import json
import logging
import secrets
from datetime import datetime, timedelta

import requests
from flask import Blueprint, current_app, jsonify, redirect, render_template, request, url_for

from src.models import Category, ContactMessage, Post, Subscriber, db, utcnow
from src.utils import make_excerpt, sanitize_html, slugify, valid_email

api_bp = Blueprint("api", __name__, url_prefix="/api")
log = logging.getLogger(__name__)


def _push_bigmailer(email: str) -> None:
    cfg = current_app.config
    if not (cfg["BIGMAILER_API_KEY"] and cfg["BIGMAILER_BRAND_ID"] and cfg["BIGMAILER_LIST_ID"]):
        return
    try:
        requests.post(
            f"https://api.bigmailer.io/v1/brands/{cfg['BIGMAILER_BRAND_ID']}/contacts",
            headers={"X-API-Key": cfg["BIGMAILER_API_KEY"], "Content-Type": "application/json"},
            json={"email": email, "list_ids": [cfg["BIGMAILER_LIST_ID"]]},
            timeout=8,
        )
    except requests.RequestException as exc:  # never block the user on a third-party failure
        log.warning("bigmailer push failed: %s", exc)


def _wants_json() -> bool:
    return request.is_json or "application/json" in request.headers.get("Accept", "")


@api_bp.route("/health")
def health():
    return jsonify(status="ok", time=utcnow().isoformat())


@api_bp.route("/subscribe", methods=["POST"])
def subscribe():
    data = request.get_json(silent=True) or request.form
    email = (data.get("email") or "").strip().lower()
    if data.get("website"):  # honeypot field filled by bots
        return jsonify(ok=True)
    if not valid_email(email):
        if _wants_json():
            return jsonify(ok=False, error="Please enter a valid email address."), 400
        return render_template("subscribed.html", ok=False, email=email), 400
    sub = Subscriber.query.filter_by(email=email).first()
    if sub:
        sub.unsubscribed_at = None
    else:
        db.session.add(Subscriber(email=email, source=(data.get("source") or "site")[:80]))
    db.session.commit()
    _push_bigmailer(email)
    if _wants_json():
        return jsonify(ok=True)
    return render_template("subscribed.html", ok=True, email=email)


@api_bp.route("/unsubscribe", methods=["POST"])
def unsubscribe():
    email = (request.form.get("email") or "").strip().lower()
    if valid_email(email):
        sub = Subscriber.query.filter_by(email=email).first()
        if sub:
            sub.unsubscribed_at = utcnow()
            db.session.commit()
    # Always confirm; never reveal whether an address was on the list.
    return render_template("unsubscribe.html", done=True, email=email)


@api_bp.route("/contact", methods=["POST"])
def contact():
    f = request.form
    if f.get("website"):
        return redirect("/contact-us/?sent=1")
    name = (f.get("name") or "").strip()[:120]
    email = (f.get("email") or "").strip()[:254]
    subject = (f.get("subject") or "").strip()[:200]
    message = (f.get("message") or "").strip()[:5000]
    if not message or not valid_email(email):
        return redirect("/contact-us/?error=1")
    db.session.add(ContactMessage(name=name, email=email, subject=subject, message=message))
    db.session.commit()
    return redirect("/contact-us/?sent=1")


# ---- content pipeline endpoints (bearer token) -------------------------------------------

def _pipeline_authorized() -> bool:
    token = current_app.config["PUBLISH_TOKEN"]
    supplied = request.headers.get("Authorization", "").removeprefix("Bearer ").strip()
    return bool(token) and secrets.compare_digest(supplied, token)


@api_bp.route("/posts/recent")
def recent_posts():
    """Titles of every story (any status) from the last N days, so the pipeline can avoid repeats."""
    if not _pipeline_authorized():
        return jsonify(error="unauthorized"), 401
    days = min(90, max(1, request.args.get("days", 14, type=int)))
    since = utcnow() - timedelta(days=days)
    rows = Post.query.filter(Post.published_at >= since).order_by(Post.published_at.desc()).all()
    return jsonify(posts=[
        {"title": p.title, "slug": p.slug, "status": p.status, "category": p.category.slug,
         "published_at": p.published_at.isoformat(), "sources": p.source_list}
        for p in rows
    ])


@api_bp.route("/publish", methods=["POST"])
def publish():
    """Create (or, with update=true, replace) a story. Defaults to a draft for human review."""
    if not _pipeline_authorized():
        return jsonify(error="unauthorized"), 401
    data = request.get_json(silent=True) or {}
    title = (data.get("title") or "").strip()[:300]
    body = sanitize_html(data.get("body_html") or "")
    if not title or not body:
        return jsonify(ok=False, error="title and body_html are required"), 400
    slug = slugify(data.get("slug") or title)
    existing = Post.query.filter_by(slug=slug).first()
    if existing and not data.get("update"):
        return jsonify(ok=False, error="slug already exists", id=existing.id, slug=slug), 409
    cat = Category.query.filter_by(slug=(data.get("category") or "politics")).first() or Category.query.filter_by(slug="politics").first()
    post = existing or Post(slug=slug)
    post.title = title
    post.body_html = body
    post.excerpt = (data.get("excerpt") or "").strip()[:500] or make_excerpt(body)
    post.category = cat
    post.author = (data.get("author") or "Staff").strip()[:120]
    post.image_url = (data.get("image_url") or "").strip()[:600]
    post.status = "published" if data.get("status") == "published" else "draft"
    post.editor_notes = (data.get("editor_notes") or "")[:5000]
    post.sources = json.dumps([
        {"label": str(s.get("label", ""))[:200], "url": str(s.get("url", ""))[:600]}
        for s in (data.get("sources") or []) if isinstance(s, dict) and s.get("url")
    ][:10])
    when = data.get("published_at")
    if when:
        try:
            post.published_at = datetime.fromisoformat(str(when).replace("Z", "+00:00")).replace(tzinfo=None)
        except ValueError:
            pass
    elif not post.published_at:
        post.published_at = utcnow()
    db.session.add(post)
    db.session.commit()
    return jsonify(ok=True, id=post.id, slug=post.slug, status=post.status, url=post.url,
                   admin_url=url_for("admin.edit_post", post_id=post.id)), (200 if existing else 201)
