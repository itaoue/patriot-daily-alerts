from datetime import timedelta

from flask import Blueprint, Response, abort, current_app, redirect, render_template, request, url_for
from sqlalchemy import or_

from src.models import Category, Page, Post, db, utcnow
from src.utils import strip_tags

public_bp = Blueprint("public", __name__)

RESERVED_SLUGS = {"admin", "api", "static", "feed", "search", "category", "sitemap.xml", "robots.txt"}


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


@public_bp.route("/")
def home():
    q = published_posts().order_by(Post.published_at.desc())
    lead = q.filter(Post.featured.is_(True)).first() or q.first()
    if not lead:
        return render_template("index.html", lead=None, top=[], latest=[], most_read=[], ticker=[], page=1, has_more=False)
    top = q.filter(Post.id != lead.id).limit(4).all()
    exclude = [lead.id] + [p.id for p in top]
    latest = q.filter(Post.id.notin_(exclude)).limit(current_app.config["POSTS_PER_PAGE"]).all()
    has_more = q.filter(Post.id.notin_(exclude)).count() > len(latest)
    ticker = q.limit(6).all()
    return render_template(
        "index.html", lead=lead, top=top, latest=latest, most_read=most_read(5), ticker=ticker, has_more=has_more
    )


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
    body = f"User-agent: *\nDisallow: /admin/\nDisallow: /api/\nSitemap: {current_app.config['SITE_URL']}/sitemap.xml\n"
    return Response(body, mimetype="text/plain")


@public_bp.route("/remove-from-our-email-list/")
def unsubscribe_page():
    return render_template("unsubscribe.html", email=request.args.get("email", ""))


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
        if not post.is_published and not request.args.get("preview"):
            abort(404)
        # lightweight popularity counter (no extra table)
        Post.query.filter_by(id=post.id).update({Post.views: Post.views + 1})
        db.session.commit()
        related = (
            published_posts().filter(Post.category_id == post.category_id, Post.id != post.id)
            .order_by(Post.published_at.desc()).limit(4).all()
        )
        prev_post = published_posts().filter(Post.published_at < post.published_at).order_by(Post.published_at.desc()).first()
        next_post = published_posts().filter(Post.published_at > post.published_at).order_by(Post.published_at.asc()).first()
        return render_template(
            "article.html", post=post, related=related, most_read=most_read(5, post.id),
            prev_post=prev_post, next_post=next_post,
        )
    page = Page.query.filter_by(slug=slug).first_or_404()
    return render_template("page.html", page=page)


@public_bp.app_errorhandler(404)
def not_found(_):
    return render_template("404.html"), 404
