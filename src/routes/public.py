import os
import re
from datetime import timedelta

from flask import (
    Blueprint,
    Response,
    abort,
    current_app,
    make_response,
    redirect,
    render_template,
    request,
    send_from_directory,
    session,
    url_for,
)
from sqlalchemy import or_

from src.models import (
    SAVINGS_BAND_KEYS,
    SAVINGS_BANDS,
    Category,
    Comment,
    Offer,
    OfferClick,
    Page,
    Poll,
    PollVote,
    Post,
    QualifierAnswer,
    Subscriber,
    db,
    offer_visible,
    utcnow,
)
from src.utils import strip_tags, valid_email

public_bp = Blueprint("public", __name__)

RESERVED_SLUGS = {"admin", "api", "static", "feed", "search", "category", "sitemap.xml", "robots.txt", "join", "welcome", "go", "poll"}


def published_posts():
    return Post.query.filter(Post.status == "published", Post.published_at <= utcnow())


def most_read(limit=5, exclude_id=None):
    q = published_posts().filter(Post.published_at >= utcnow() - timedelta(days=45))
    if exclude_id:
        q = q.filter(Post.id != exclude_id)
    rows = q.order_by(Post.views.desc(), Post.published_at.desc()).limit(limit).all()
    if len(rows) < limit:  # fresh site: fall back to latest
        rows = published_posts().order_by(Post.published_at.desc()).limit(limit).all()
    return rows


@public_bp.app_context_processor
def inject_sidebar_posts():
    if request.path.startswith(("/static/", "/api/", "/admin/")):
        return {}
    recent = published_posts().order_by(Post.published_at.desc()).limit(5).all()
    return {"ticker_posts": recent, "recent_posts": recent}


@public_bp.route("/")
def home():
    q = published_posts().order_by(Post.published_at.desc())
    lead = q.filter(Post.featured.is_(True)).first() or q.first()
    if not lead:
        return render_template("index.html", lead=None, top=[], latest=[], most_read=[], has_more=False)
    top = q.filter(Post.id != lead.id).limit(4).all()
    exclude = [lead.id] + [p.id for p in top]
    latest = q.filter(Post.id.notin_(exclude)).limit(current_app.config["POSTS_PER_PAGE"]).all()
    has_more = q.filter(Post.id.notin_(exclude)).count() > len(latest)
    return render_template("index.html", lead=lead, top=top, latest=latest, most_read=most_read(5), has_more=has_more)


@public_bp.route("/latest/")
def latest():
    page = max(1, request.args.get("page", 1, type=int))
    pagination = published_posts().order_by(Post.published_at.desc()).paginate(
        page=page, per_page=current_app.config["POSTS_PER_PAGE"], error_out=False
    )
    return render_template(
        "archive.html", title="Latest Stories", description="Every story, newest first.",
        pagination=pagination, base_url=url_for("public.latest"), most_read=most_read(5),
    )


@public_bp.route("/category/<slug>/")
def category(slug):
    if slug == "latest-news":  # legacy WordPress URL: that category held every post
        return redirect(url_for("public.latest", page=request.args.get("page")), 301)
    cat = Category.query.filter_by(slug=slug).first_or_404()
    page = max(1, request.args.get("page", 1, type=int))
    pagination = (
        published_posts().filter(Post.category_id == cat.id).order_by(Post.published_at.desc())
        .paginate(page=page, per_page=current_app.config["POSTS_PER_PAGE"], error_out=False)
    )
    return render_template(
        "archive.html", title=cat.name, description=cat.description, category=cat,
        pagination=pagination, base_url=url_for("public.category", slug=slug), most_read=most_read(5),
    )


@public_bp.route("/search/")
def search():
    term = (request.args.get("q") or "").strip()[:100]
    page = max(1, request.args.get("page", 1, type=int))
    pagination = None
    if term:
        like = f"%{term}%"
        pagination = (
            published_posts().filter(or_(Post.title.ilike(like), Post.excerpt.ilike(like)))
            .order_by(Post.published_at.desc())
            .paginate(page=page, per_page=current_app.config["POSTS_PER_PAGE"], error_out=False)
        )
    return render_template(
        "archive.html", title=f'Results for "{term}"' if term else "Search",
        description=f"{pagination.total} stories found." if pagination else "Search every story we have published.",
        pagination=pagination, base_url=url_for("public.search", q=term), search_term=term, most_read=most_read(5),
    )


@public_bp.route("/feed/")
def feed():
    posts = published_posts().order_by(Post.published_at.desc()).limit(30).all()
    xml = render_template("feed.xml", posts=posts, strip_tags=strip_tags, now=utcnow())
    return Response(xml, mimetype="application/rss+xml")


@public_bp.route("/sitemap.xml")
def sitemap():
    posts = published_posts().order_by(Post.published_at.desc()).limit(5000).all()
    cats = Category.query.all()
    pages = Page.query.all()
    xml = render_template("sitemap.xml", posts=posts, categories=cats, pages=pages)
    return Response(xml, mimetype="application/xml")


@public_bp.route("/robots.txt")
def robots():
    body = f"User-agent: *\nDisallow: /admin/\nDisallow: /api/\nDisallow: /go/\nSitemap: {current_app.config['SITE_URL']}/sitemap.xml\n"
    return Response(body, mimetype="text/plain")


def _voter_id() -> str:
    import hashlib

    fwd = request.headers.get("X-Forwarded-For", "").split(",")[0].strip()
    ip = request.headers.get("CF-Connecting-IP") or fwd or request.remote_addr or ""
    return hashlib.sha256(f"{ip}|{request.user_agent.string}".encode()).hexdigest()[:40]


@public_bp.route("/poll/<int:poll_id>/")
def poll_results(poll_id):
    poll = Poll.query.get_or_404(poll_id)
    contact = _contact_id()
    if "c" in request.args:  # keep the contact id out of analytics / browser history: cookie it, then clean URL
        args = {k: v for k, v in request.args.items() if k != "c"}
        resp = redirect(url_for("public.poll_results", poll_id=poll.id, **args))
        _remember_reader(resp, contact=contact)
        return resp
    voted = request.cookies.get(f"pv{poll.id}")
    band = _savings_band()
    if not band and contact:  # answered earlier from another device / browser
        prior = QualifierAnswer.query.filter_by(contact_id=contact, question="savings").order_by(QualifierAnswer.updated_at.desc()).first()
        band = prior.answer if prior else ""
    offers = [o for o in Offer.query.filter_by(active=True).order_by(Offer.weight.desc(), Offer.id.desc())
              if offer_visible(o.audience or "all", band)][:3]
    if offers and not _is_bot():
        Offer.query.filter(Offer.id.in_([o.id for o in offers])).update(
            {Offer.impressions: Offer.impressions + 1}, synchronize_session=False)
        db.session.commit()
    resp = make_response(render_template(
        "poll.html", poll=poll, results=poll.results(), total=poll.votes.count(), voted=voted,
        just_voted=request.args.get("voted") is not None, latest=most_read(4), offers=offers,
        band=band, savings_bands=SAVINGS_BANDS, just_answered=request.args.get("answered") is not None,
        sponsor=current_app.config.get("POLL_SPONSOR")))
    _remember_reader(resp, contact=contact, band=band)
    return resp


@public_bp.route("/poll/<int:poll_id>/vote/<int:choice>/")
def poll_vote(poll_id, choice):
    """Links from the newsletter land here (GET, since email can't POST).

    ?c=*|_ID|* (BigMailer contact id) makes it one vote per subscriber, last click wins: link scanners that
    pre-open the email can't lock in a choice. Without it: one vote per browser / voter hash."""
    poll = Poll.query.get_or_404(poll_id)
    if choice < 0 or choice >= len(poll.option_list):
        abort(404)
    voter, contact = _voter_id(), _contact_id()
    if not _is_bot():
        row = PollVote.query.filter_by(poll_id=poll.id, contact_id=contact).first() if contact else None
        if row:
            row.choice = choice
        elif not (request.cookies.get(f"pv{poll.id}") or PollVote.query.filter_by(poll_id=poll.id, voter=voter).first()):
            db.session.add(PollVote(poll_id=poll.id, choice=choice, voter=voter, contact_id=contact))
        db.session.commit()
    resp = redirect(url_for("public.poll_results", poll_id=poll.id, voted=1))
    resp.set_cookie(f"pv{poll.id}", str(choice), max_age=60 * 60 * 24 * 90, samesite="Lax")
    _remember_reader(resp, contact=contact)
    return resp


_CONTACT_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
_YEAR = 60 * 60 * 24 * 365


def _contact_id() -> str:
    """BigMailer contact id from the email link (?c=) or the cookie it left. Unfilled merge tags (web copy) are ignored."""
    for value in (request.args.get("c", ""), request.cookies.get("bmc", "")):
        value = value.strip().lower()
        if _CONTACT_RE.match(value):
            return value
    return ""


def _remember_reader(resp, contact: str = "", band: str = "") -> None:
    if contact and request.cookies.get("bmc") != contact:
        resp.set_cookie("bmc", contact, max_age=_YEAR, samesite="Lax", httponly=True)
    if band and request.cookies.get("q_sav") != band:
        resp.set_cookie("q_sav", band, max_age=_YEAR, samesite="Lax", httponly=True)


def _savings_band() -> str:
    band = request.cookies.get("q_sav", "")
    return band if band in SAVINGS_BAND_KEYS else ""


@public_bp.route("/poll/<int:poll_id>/qualify", methods=["POST"])
def poll_qualify(poll_id):
    """Optional post-vote question (savings band). Decides which offer cards the reader sees; one row per browser,
    or per subscriber when they came from the email. Subscribers' answers are copied to a BigMailer field for segments."""
    import secrets

    from src.routes.api import set_bigmailer_field

    poll = Poll.query.get_or_404(poll_id)
    answer = request.form.get("savings", "")
    if answer not in SAVINGS_BAND_KEYS or request.form.get("website"):  # honeypot
        return redirect(url_for("public.poll_results", poll_id=poll.id))
    rid = request.cookies.get("rid", "")
    if not (len(rid) == 32 and rid.isalnum()):
        rid = secrets.token_hex(16)
    contact = _contact_id()
    row = QualifierAnswer.query.filter_by(contact_id=contact, question="savings").first() if contact else None
    row = row or QualifierAnswer.query.filter_by(rid=rid, question="savings").first()
    if row:
        row.answer, row.poll_id = answer, poll.id
        row.contact_id = row.contact_id or contact
    else:
        db.session.add(QualifierAnswer(rid=rid, voter=_voter_id(), poll_id=poll.id, question="savings", answer=answer, contact_id=contact))
    db.session.commit()
    if contact:
        set_bigmailer_field(contact, current_app.config["BIGMAILER_SAVINGS_FIELD"], answer)
    resp = redirect(url_for("public.poll_results", poll_id=poll.id, answered=1, _anchor="offers"))
    resp.set_cookie("rid", rid, max_age=_YEAR, samesite="Lax", httponly=True)
    resp.set_cookie("q_sav", answer, max_age=_YEAR, samesite="Lax", httponly=True)
    return resp


# email security scanners (Outlook Safe Links, Proofpoint, Mimecast, Barracuda...) and crawlers pre-fetch links
_BOT_UA = re.compile(r"bot|crawl|spider|slurp|preview|scan|proofpoint|mimecast|barracuda|safelinks|python|curl|wget|"
                     r"httpclient|okhttp|headless|phantom|monitor|checker|fetch", re.I)


def _is_bot() -> bool:
    ua = request.user_agent.string or ""
    return request.method == "HEAD" or not ua or bool(_BOT_UA.search(ua))


@public_bp.route("/go/<int:offer_id>/")
def offer_click(offer_id):
    """Tracked redirect for offer cards. ?p=<poll id> ties the click to the poll page it came from."""
    offer = Offer.query.get_or_404(offer_id)
    if not offer.active:
        return redirect(url_for("public.home"))
    poll_id = request.args.get("p", type=int)
    click = OfferClick(offer_id=offer.id, poll_id=poll_id, voter=_voter_id(),
                       user_agent=(request.user_agent.string or "")[:300], is_bot=_is_bot(), segment=_savings_band())
    db.session.add(click)
    db.session.commit()
    subid = f"pda-o{offer.id}-p{poll_id or 0}-c{click.id}-s{click.segment or 'none'}"
    resp = redirect(offer.url.replace("{subid}", subid), 302)
    resp.headers["X-Robots-Tag"] = "noindex, nofollow"
    resp.headers["Cache-Control"] = "no-store"
    return resp


@public_bp.route("/<slug>/comment", methods=["POST"])
def add_comment(slug):
    """Reader comment form. Honeypot + per-visitor rate limit; held for review unless COMMENTS_AUTO_APPROVE."""
    if not current_app.config["COMMENTS_ENABLED"]:
        abort(404)
    post = Post.query.filter_by(slug=slug).first_or_404()
    if not post.is_published:
        abort(404)
    f = request.form
    back = url_for("public.article_or_page", slug=slug)
    if f.get("website"):  # honeypot
        return redirect(back + "?comment=ok#comments")
    name = (f.get("name") or "").strip()[:60]
    body = (f.get("body") or "").strip()[:2000]
    email = (f.get("email") or "").strip()[:254]
    if len(name) < 2 or len(body) < 3 or (email and not valid_email(email)):
        return redirect(back + "?comment=invalid#comment-form")
    voter = _voter_id()
    from datetime import timedelta as _td

    recent = Comment.query.filter(Comment.ip_hash == voter, Comment.created_at >= utcnow() - _td(hours=1)).count()
    if recent >= current_app.config["COMMENTS_PER_HOUR"]:
        return redirect(back + "?comment=slow#comment-form")
    links = body.lower().count("http")
    auto = current_app.config["COMMENTS_AUTO_APPROVE"] and links == 0
    comment = Comment(post_id=post.id, name=name, email=email, body=body, ip_hash=voter,
                      user_agent=request.user_agent.string[:300], status="approved" if auto else "pending")
    db.session.add(comment)
    db.session.commit()
    from src.notify import notify_comment

    notify_comment(comment)
    return redirect(back + ("?comment=ok#comments" if auto else "?comment=pending#comments"))


@public_bp.route("/wp-content/uploads/<path:filename>")
def legacy_upload(filename):
    """Serve images mirrored from the old WordPress host (tools/migrate_images.py) at their original URLs."""
    folder = os.path.join(current_app.static_folder, "uploads")
    return send_from_directory(folder, filename, max_age=60 * 60 * 24 * 365)


# ---- subscription landing page + thank-you page --------------------------------------------

UTM_KEYS = ("utm_source", "utm_medium", "utm_campaign", "utm_content")


def _utm_from_request() -> dict:
    return {k: (request.args.get(k) or "").strip()[:40] for k in UTM_KEYS}


def _latest_newsletter_issue() -> str:
    """Filename of the newest "View online" page in static/newsletters/, used as the live preview on /join/."""
    folder = os.path.join(current_app.static_folder, "newsletters")
    try:
        names = sorted(n for n in os.listdir(folder) if n.endswith(".html"))
    except OSError:
        return ""
    return names[-1] if names else ""


def _landing_copy() -> dict:
    cfg = dict(current_app.config.get("LANDING") or {})
    variant = (cfg.get("variants") or {}).get(request.args.get("h", ""))
    if variant:
        cfg.update({k: v for k, v in variant.items() if v})
    return cfg


@public_bp.route("/join/")
def join():
    """Standalone subscription landing page for paid and referral traffic. Headline variant via ?h=<key>."""
    return render_template(
        "join.html", copy=_landing_copy(), utm=_utm_from_request(), variant=request.args.get("h", "")[:40],
        issue=_latest_newsletter_issue(),
    )


@public_bp.route("/welcome/")
def welcome():
    """Thank-you page after a landing-page signup: add-to-contacts nudge, today's poll, share links, top stories."""
    from src.welcome_email import enabled as welcome_email_enabled

    poll = Poll.query.order_by(Poll.created_at.desc()).first()
    voted = request.cookies.get(f"pv{poll.id}") if poll else None
    latest = published_posts().order_by(Post.published_at.desc()).limit(3).all()
    return render_template("welcome.html", copy=current_app.config.get("LANDING") or {}, poll=poll, voted=voted,
                           latest=latest, emailed=welcome_email_enabled())


@public_bp.route("/remove-from-our-email-list/")
def unsubscribe_page():
    return render_template("unsubscribe.html", email=request.args.get("email", ""))


@public_bp.route("/remove-from-our-email-list/<token>/", methods=["GET", "POST"])
def unsubscribe_token(token):
    """Target of the List-Unsubscribe header on the welcome email.

    A POST is the mailbox provider's one-click unsubscribe and takes effect immediately. A GET only pre-fills the
    confirmation form: link scanners follow GETs, and they must not be able to unsubscribe a reader by accident.

    A token that no longer verifies (SECRET_KEY rotated, link mangled in transit) must never dead-end: a reader who
    cannot unsubscribe reports spam instead, which costs the whole list far more than the unsubscribe would have. So a
    bad token falls back to the plain form where they can type their address. A POST still fails, because there is no
    way to tell whom to remove and reporting success would be a lie.
    """
    from src.welcome_email import verify_unsubscribe_token

    email = verify_unsubscribe_token(token)
    if not email:
        if request.method == "POST":
            abort(400)
        return redirect(url_for("public.unsubscribe_page"), 302)
    if request.method == "POST":
        sub = Subscriber.query.filter_by(email=email).first()
        if sub and not sub.unsubscribed_at:
            sub.unsubscribed_at = utcnow()
            db.session.commit()
        return render_template("unsubscribe.html", done=True, email=email)
    return render_template("unsubscribe.html", email=email, token=token)


@public_bp.route("/contact-us/")
def contact_page():
    page = Page.query.filter_by(slug="contact-us").first()
    return render_template("contact.html", page=page)


@public_bp.route("/<slug>/")
def article_or_page(slug):
    if slug in RESERVED_SLUGS:
        abort(404)
    post = Post.query.filter_by(slug=slug).first()
    if post:
        if not post.is_published and not (request.args.get("preview") and session.get("admin")):
            abort(404)  # drafts are visible only to a signed-in editor
        # lightweight popularity counter (no extra table)
        Post.query.filter_by(id=post.id).update({Post.views: Post.views + 1})
        db.session.commit()
        related = (
            published_posts().filter(Post.category_id == post.category_id, Post.id != post.id)
            .order_by(Post.published_at.desc()).limit(4).all()
        )
        prev_post = published_posts().filter(Post.published_at < post.published_at).order_by(Post.published_at.desc()).first()
        next_post = published_posts().filter(Post.published_at > post.published_at).order_by(Post.published_at.asc()).first()
        comments = post.comments.filter_by(status="approved").order_by(Comment.created_at.asc()).limit(300).all()
        return render_template(
            "article.html", post=post, related=related, most_read=most_read(5, post.id),
            prev_post=prev_post, next_post=next_post, comments=comments,
            comment_state=request.args.get("comment"), comments_enabled=current_app.config["COMMENTS_ENABLED"],
        )
    page = Page.query.filter_by(slug=slug).first_or_404()
    return render_template("page.html", page=page)


@public_bp.app_errorhandler(404)
def not_found(_):
    return render_template("404.html"), 404
