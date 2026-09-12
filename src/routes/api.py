import json
import logging
import re
import secrets
from datetime import datetime, timedelta

import requests
from flask import Blueprint, current_app, jsonify, redirect, render_template, request, url_for

from src.models import Category, ContactMessage, Poll, Post, Subscriber, db, utcnow
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


def _signup_source(data) -> str:
    """`source` plus UTM source/campaign, e.g. "landing:taboola:border", so the CSV export shows what each channel cost."""
    parts = [(data.get("source") or "site")[:40]]
    parts += [(data.get(k) or "").strip()[:20] for k in ("utm_source", "utm_campaign")]
    joined = ":".join(p for p in parts if p).lower()
    return re.sub(r"[^a-z0-9_:.-]", "", joined)[:80] or "site"


def _safe_next(data) -> str:
    """Relative path to redirect to after signup (the landing page sends /welcome/). Never an external URL."""
    nxt = (data.get("next") or "").strip()
    return nxt if nxt.startswith("/") and not nxt.startswith("//") and "\\" not in nxt else ""


@api_bp.route("/subscribe", methods=["POST"])
def subscribe():
    data = request.get_json(silent=True) or request.form
    email = (data.get("email") or "").strip().lower()
    nxt = _safe_next(data)
    if data.get("website"):  # honeypot field filled by bots
        return jsonify(ok=True, next=nxt) if _wants_json() else redirect(nxt or "/")
    if not valid_email(email):
        if _wants_json():
            return jsonify(ok=False, error="Please enter a valid email address."), 400
        return render_template("subscribed.html", ok=False, email=email), 400
    sub = Subscriber.query.filter_by(email=email).first()
    is_new = sub is None or sub.unsubscribed_at is not None  # re-submitting an active address must not send twice
    if sub:
        sub.unsubscribed_at = None
    else:
        db.session.add(Subscriber(email=email, source=_signup_source(data)))
    db.session.commit()
    _push_bigmailer(email)
    if is_new:
        from src import welcome_email

        welcome_email.send(email)
    if _wants_json():
        return jsonify(ok=True, next=nxt)
    if nxt:
        return redirect(nxt)
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
    msg = ContactMessage(name=name, email=email, subject=subject, message=message)
    db.session.add(msg)
    db.session.commit()
    from src.notify import notify_contact

    notify_contact(msg)
    return redirect("/contact-us/?sent=1")


# ---- content pipeline endpoints (bearer token) -------------------------------------------

def _pipeline_authorized() -> bool:
    token = current_app.config["PUBLISH_TOKEN"]
    supplied = request.headers.get("Authorization", "").removeprefix("Bearer ").strip()
    return bool(token) and secrets.compare_digest(supplied, token)


@api_bp.route("/scheduler")
def scheduler_status():
    """What the in-app workflow scheduler is set to and what it has dispatched lately."""
    if not _pipeline_authorized():
        return jsonify(error="unauthorized"), 401
    from src.scheduler import status

    return jsonify(status(current_app._get_current_object()))


@api_bp.route("/posts/recent")
def recent_posts():
    """Titles of every story (any status) from the last N days, so the pipeline can avoid repeats."""
    if not _pipeline_authorized():
        return jsonify(error="unauthorized"), 401
    days = min(90, max(1, request.args.get("days", 14, type=int)))
    since = utcnow() - timedelta(days=days)
    rows = Post.query.filter(Post.published_at >= since).order_by(Post.published_at.desc()).all()
    return jsonify(posts=[
        {"title": p.title, "slug": p.slug, "status": p.status, "category": p.category.slug, "author": p.author,
         "published_at": p.published_at.isoformat(), "sources": p.source_list, "editor_notes": p.editor_notes or "",
         "url": p.url, "image_url": p.image_url, "excerpt": p.excerpt, "body_html": p.body_html}
        for p in rows
    ])


@api_bp.route("/publish", methods=["POST"])
def publish():
    """Create a story (defaults to a draft for human review). With update=true and an existing slug,
    only the fields present in the request are changed, so status/category can be flipped alone."""
    if not _pipeline_authorized():
        return jsonify(error="unauthorized"), 401
    data = request.get_json(silent=True) or {}
    slug = slugify(data.get("slug") or data.get("title") or "")
    existing = Post.query.filter_by(slug=slug).first() if slug else None
    if data.get("delete"):
        if not existing:
            return jsonify(ok=False, error="no such slug"), 404
        db.session.delete(existing)
        db.session.commit()
        return jsonify(ok=True, deleted=slug)
    if existing and not data.get("update"):
        return jsonify(ok=False, error="slug already exists", id=existing.id, slug=slug), 409
    if not existing and not ((data.get("title") or "").strip() and (data.get("body_html") or "").strip()):
        return jsonify(ok=False, error="title and body_html are required"), 400
    post = existing or Post(slug=slug)
    if "title" in data:
        post.title = (data["title"] or "").strip()[:300]
    if "body_html" in data:
        post.body_html = sanitize_html(data["body_html"] or "")
    if "excerpt" in data or not post.excerpt:
        post.excerpt = (data.get("excerpt") or "").strip()[:500] or make_excerpt(post.body_html)
    if "category" in data or not post.category_id:
        post.category = (Category.query.filter_by(slug=(data.get("category") or "politics")).first()
                         or Category.query.filter_by(slug="politics").first())
    if "author" in data or not post.author:
        post.author = (data.get("author") or "Staff").strip()[:120]
    if "image_url" in data:
        post.image_url = (data["image_url"] or "").strip()[:600]
    if "status" in data or not existing:
        was_published = existing is not None and existing.status == "published"
        post.status = "published" if data.get("status") == "published" else "draft"
        if post.status == "published" and not was_published and "published_at" not in data:
            post.published_at = utcnow()  # approving a draft stamps it with the publish time
    if "editor_notes" in data:
        post.editor_notes = (data["editor_notes"] or "")[:5000]
    if "sources" in data:
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


@api_bp.route("/polls", methods=["POST"])
def create_poll():
    """Create the day's poll (used by the newsletter builder). Returns the vote/results URLs."""
    if not _pipeline_authorized():
        return jsonify(error="unauthorized"), 401
    data = request.get_json(silent=True) or {}
    if data.get("delete"):
        poll = Poll.query.get(int(data.get("id") or 0))
        if not poll and data.get("campaign"):
            poll = Poll.query.filter_by(campaign=data["campaign"]).first()
        if not poll:
            return jsonify(ok=False, error="no such poll"), 404
        db.session.delete(poll)
        db.session.commit()
        return jsonify(ok=True, deleted=poll.id)
    question = (data.get("question") or "").strip()[:300]
    options = [str(o).strip()[:120] for o in (data.get("options") or []) if str(o).strip()][:4]
    if not question or len(options) < 2:
        return jsonify(ok=False, error="question and 2-4 options are required"), 400
    campaign = (data.get("campaign") or "")[:40]
    poll = Poll.query.filter_by(campaign=campaign).first() if campaign else None
    if poll and not data.get("update"):
        pass  # same issue rebuilt: reuse the existing poll so links stay stable
    else:
        poll = poll or Poll(campaign=campaign)
        poll.question, poll.options = question, json.dumps(options)
        db.session.add(poll)
        db.session.commit()
    site = current_app.config["SITE_URL"]
    return jsonify(ok=True, id=poll.id, question=poll.question, options=poll.option_list,
                   results_url=f"{site}{url_for('public.poll_results', poll_id=poll.id)}",
                   vote_urls=[f"{site}{url_for('public.poll_vote', poll_id=poll.id, choice=i)}" for i in range(len(poll.option_list))])


@api_bp.route("/polls/<int:poll_id>")
def poll_results_api(poll_id):
    if not _pipeline_authorized():
        return jsonify(error="unauthorized"), 401
    poll = Poll.query.get_or_404(poll_id)
    return jsonify(id=poll.id, campaign=poll.campaign, question=poll.question, results=poll.results(), total=poll.votes.count())
