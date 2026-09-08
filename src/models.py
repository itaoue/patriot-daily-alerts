from datetime import datetime, timezone

from flask_sqlalchemy import SQLAlchemy

db = SQLAlchemy()


def utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


class Category(db.Model):
    __tablename__ = "categories"
    id = db.Column(db.Integer, primary_key=True)
    slug = db.Column(db.String(80), unique=True, nullable=False, index=True)
    name = db.Column(db.String(120), nullable=False)
    description = db.Column(db.String(300), default="")
    posts = db.relationship("Post", back_populates="category", lazy="dynamic")


class Post(db.Model):
    __tablename__ = "posts"
    id = db.Column(db.Integer, primary_key=True)
    wp_id = db.Column(db.Integer, unique=True, nullable=True, index=True)
    slug = db.Column(db.String(200), unique=True, nullable=False, index=True)
    title = db.Column(db.String(300), nullable=False)
    excerpt = db.Column(db.Text, default="")
    body_html = db.Column(db.Text, default="")
    image_url = db.Column(db.String(600), default="")
    author = db.Column(db.String(120), default="Staff")
    category_id = db.Column(db.Integer, db.ForeignKey("categories.id"), nullable=False)
    category = db.relationship("Category", back_populates="posts")
    status = db.Column(db.String(20), default="published", index=True)  # published | draft
    featured = db.Column(db.Boolean, default=False)
    views = db.Column(db.Integer, default=0)
    editor_notes = db.Column(db.Text, default="")  # notes from the automated editor pass / pipeline
    sources = db.Column(db.Text, default="")  # JSON list of {"label", "url"}
    published_at = db.Column(db.DateTime, default=utcnow, index=True)
    updated_at = db.Column(db.DateTime, default=utcnow, onupdate=utcnow)

    @property
    def url(self) -> str:
        return f"/{self.slug}/"

    @property
    def is_published(self) -> bool:
        return self.status == "published" and self.published_at <= utcnow()

    @property
    def source_list(self) -> list:
        import json

        try:
            return json.loads(self.sources) if self.sources else []
        except ValueError:
            return []

    @property
    def reading_minutes(self) -> int:
        words = len((self.body_html or "").split())
        return max(1, round(words / 220))


class Page(db.Model):
    __tablename__ = "pages"
    id = db.Column(db.Integer, primary_key=True)
    slug = db.Column(db.String(120), unique=True, nullable=False, index=True)
    title = db.Column(db.String(200), nullable=False)
    body_html = db.Column(db.Text, default="")
    updated_at = db.Column(db.DateTime, default=utcnow, onupdate=utcnow)


class Subscriber(db.Model):
    __tablename__ = "subscribers"
    id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.String(254), unique=True, nullable=False, index=True)
    source = db.Column(db.String(80), default="site")
    created_at = db.Column(db.DateTime, default=utcnow)
    unsubscribed_at = db.Column(db.DateTime, nullable=True)


class ContactMessage(db.Model):
    __tablename__ = "contact_messages"
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120), default="")
    email = db.Column(db.String(254), default="")
    subject = db.Column(db.String(200), default="")
    message = db.Column(db.Text, default="")
    created_at = db.Column(db.DateTime, default=utcnow)


class Setting(db.Model):
    """Small key/value store for app state (e.g. import progress)."""

    __tablename__ = "settings"
    key = db.Column(db.String(80), primary_key=True)
    value = db.Column(db.Text, default="")


class Poll(db.Model):
    """A one-question reader poll, usually created by the newsletter builder for that day's issue."""

    __tablename__ = "polls"
    id = db.Column(db.Integer, primary_key=True)
    campaign = db.Column(db.String(40), default="", index=True)  # newsletter issue id, e.g. 2026-09-08-daily
    question = db.Column(db.String(300), nullable=False)
    options = db.Column(db.Text, nullable=False)  # JSON list of option labels
    created_at = db.Column(db.DateTime, default=utcnow)
    votes = db.relationship("PollVote", back_populates="poll", lazy="dynamic", cascade="all, delete-orphan")

    @property
    def option_list(self) -> list:
        import json

        return json.loads(self.options or "[]")

    def results(self) -> list:
        from sqlalchemy import func

        counts = dict(db.session.query(PollVote.choice, func.count()).filter(PollVote.poll_id == self.id).group_by(PollVote.choice).all())
        total = sum(counts.values())
        return [{"index": i, "label": label, "votes": counts.get(i, 0), "pct": round(100 * counts.get(i, 0) / total) if total else 0}
                for i, label in enumerate(self.option_list)]


class PollVote(db.Model):
    __tablename__ = "poll_votes"
    id = db.Column(db.Integer, primary_key=True)
    poll_id = db.Column(db.Integer, db.ForeignKey("polls.id"), nullable=False, index=True)
    poll = db.relationship("Poll", back_populates="votes")
    choice = db.Column(db.Integer, nullable=False)
    voter = db.Column(db.String(64), default="", index=True)  # sha256 of ip+ua, truncated; one vote per voter per poll
    created_at = db.Column(db.DateTime, default=utcnow)
