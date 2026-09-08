import secrets
from datetime import datetime
from functools import wraps

from flask import Blueprint, abort, current_app, flash, jsonify, redirect, render_template, request, session, url_for

from src.models import Category, ContactMessage, Page, Poll, Post, Subscriber, db, utcnow
from src.utils import make_excerpt, sanitize_html, slugify

admin_bp = Blueprint("admin", __name__, url_prefix="/admin")


def login_required(fn):
    @wraps(fn)
    def wrapper(*args, **kwargs):
        if not session.get("admin"):
            return redirect(url_for("admin.login", next=request.path))
        if request.method == "POST" and request.form.get("csrf") != session.get("csrf"):
            abort(400)
        return fn(*args, **kwargs)

    return wrapper


@admin_bp.before_request
def ensure_csrf():
    session.setdefault("csrf", secrets.token_urlsafe(24))


@admin_bp.context_processor
def admin_globals():
    return {"csrf": session.get("csrf", "")}


@admin_bp.route("/login", methods=["GET", "POST"])
def login():
    password = current_app.config["ADMIN_PASSWORD"]
    if request.method == "POST":
        if password and secrets.compare_digest(request.form.get("password", ""), password):
            session["admin"] = True
            session.permanent = False
            return redirect(request.args.get("next") or url_for("admin.dashboard"))
        flash("Wrong password.", "error")
    return render_template("admin/login.html", configured=bool(password))


@admin_bp.route("/logout", methods=["POST"])
def logout():
    session.clear()
    return redirect(url_for("public.home"))


@admin_bp.route("/")
@login_required
def dashboard():
    stats = {
        "posts": Post.query.count(),
        "drafts": Post.query.filter_by(status="draft").count(),
        "subscribers": Subscriber.query.filter(Subscriber.unsubscribed_at.is_(None)).count(),
        "messages": ContactMessage.query.count(),
    }
    recent = Post.query.order_by(Post.updated_at.desc()).limit(15).all()
    drafts = Post.query.filter_by(status="draft").order_by(Post.published_at.desc()).limit(20).all()
    return render_template("admin/dashboard.html", stats=stats, recent=recent, drafts=drafts)


@admin_bp.route("/posts/")
@login_required
def posts():
    page = max(1, request.args.get("page", 1, type=int))
    term = (request.args.get("q") or "").strip()
    status = request.args.get("status", "")
    q = Post.query
    if term:
        q = q.filter(Post.title.ilike(f"%{term}%"))
    if status in ("draft", "published"):
        q = q.filter(Post.status == status)
    pagination = q.order_by(Post.published_at.desc()).paginate(page=page, per_page=25, error_out=False)
    return render_template("admin/posts.html", pagination=pagination, term=term, status=status)


def _fill_post(post: Post, form) -> None:
    post.title = form.get("title", "").strip()[:300]
    slug = slugify(form.get("slug") or post.title)
    if Post.query.filter(Post.slug == slug, Post.id != post.id).first():
        slug = f"{slug}-{secrets.token_hex(2)}"
    post.slug = slug
    post.body_html = sanitize_html(form.get("body_html", ""))
    post.excerpt = form.get("excerpt", "").strip()[:500] or make_excerpt(post.body_html)
    post.image_url = form.get("image_url", "").strip()[:600]
    post.author = form.get("author", "Staff").strip()[:120] or "Staff"
    post.category_id = int(form.get("category_id"))
    if form.get("status") == "published" and post.status != "published" and not form.get("published_at", "").strip():
        post.published_at = utcnow()  # approving a draft publishes it now, not at the draft's creation time
    post.status = "published" if form.get("status") == "published" else "draft"
    post.featured = form.get("featured") == "on"
    when = form.get("published_at", "").strip()
    if when:
        try:
            post.published_at = datetime.fromisoformat(when)
        except ValueError:
            pass
    elif not post.published_at:
        post.published_at = utcnow()


@admin_bp.route("/posts/new", methods=["GET", "POST"])
@login_required
def new_post():
    if request.method == "POST":
        post = Post(published_at=utcnow())
        _fill_post(post, request.form)
        db.session.add(post)
        db.session.commit()
        flash("Story saved.", "ok")
        return redirect(url_for("admin.edit_post", post_id=post.id))
    return render_template("admin/edit_post.html", post=None, categories=Category.query.all())


@admin_bp.route("/posts/<int:post_id>", methods=["GET", "POST"])
@login_required
def edit_post(post_id):
    post = Post.query.get_or_404(post_id)
    if request.method == "POST":
        if request.form.get("action") == "delete":
            db.session.delete(post)
            db.session.commit()
            flash("Story deleted.", "ok")
            return redirect(url_for("admin.posts"))
        _fill_post(post, request.form)
        db.session.commit()
        flash("Story saved.", "ok")
        return redirect(url_for("admin.edit_post", post_id=post.id))
    return render_template("admin/edit_post.html", post=post, categories=Category.query.all())


@admin_bp.route("/pages/")
@login_required
def pages():
    return render_template("admin/pages.html", pages=Page.query.order_by(Page.title).all())


@admin_bp.route("/pages/<int:page_id>", methods=["GET", "POST"])
@login_required
def edit_page(page_id):
    page = Page.query.get_or_404(page_id)
    if request.method == "POST":
        page.title = request.form.get("title", "").strip()[:200]
        page.body_html = sanitize_html(request.form.get("body_html", ""))
        db.session.commit()
        flash("Page saved.", "ok")
    return render_template("admin/edit_page.html", page=page)


@admin_bp.route("/subscribers/")
@login_required
def subscribers():
    page = max(1, request.args.get("page", 1, type=int))
    pagination = Subscriber.query.order_by(Subscriber.created_at.desc()).paginate(page=page, per_page=50, error_out=False)
    return render_template("admin/subscribers.html", pagination=pagination)


@admin_bp.route("/subscribers/export.csv")
@login_required
def export_subscribers():
    rows = ["email,source,created_at,unsubscribed_at"]
    for s in Subscriber.query.order_by(Subscriber.created_at).all():
        rows.append(f"{s.email},{s.source},{s.created_at.isoformat()},{s.unsubscribed_at.isoformat() if s.unsubscribed_at else ''}")
    return "\n".join(rows), 200, {"Content-Type": "text/csv", "Content-Disposition": "attachment; filename=subscribers.csv"}


@admin_bp.route("/messages/")
@login_required
def messages():
    msgs = ContactMessage.query.order_by(ContactMessage.created_at.desc()).limit(200).all()
    return render_template("admin/messages.html", messages=msgs)


@admin_bp.route("/import", methods=["GET", "POST"])
@login_required
def import_wp():
    from src.importer import get_status, start_background_import

    if request.method == "POST":
        source = (request.form.get("source") or "https://patriotdailyalerts.com").strip().rstrip("/")
        if not source.startswith("https://"):
            abort(400)
        max_posts = max(0, request.form.get("max_posts", 300, type=int))
        app = current_app._get_current_object()
        if start_background_import(app, source, max_posts):
            flash("Import started. This page refreshes itself; you can leave and come back.", "ok")
        else:
            flash("An import is already running.", "error")
        return redirect(url_for("admin.import_wp"))
    return render_template("admin/import.html", status=get_status(), post_count=Post.query.count(), page_count=Page.query.count())


@admin_bp.route("/import/status")
@login_required
def import_status():
    from src.importer import get_status

    return jsonify({**get_status(), "posts_in_db": Post.query.count()})


@admin_bp.route("/polls/")
@login_required
def polls():
    rows = Poll.query.order_by(Poll.created_at.desc()).limit(60).all()
    return render_template("admin/polls.html", polls=[(p, p.results(), p.votes.count()) for p in rows])
