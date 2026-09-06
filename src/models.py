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
    published_at = db.Column(db.DateTime, default=utcnow, index=True)
    updated_at = db.Column(db.DateTime, default=utcnow, onupdate=utcnow)

    @property
    def url(self) -> str:
        return f"/{self.slug}/"

    @property
    def is_published(self) -> bool:
        return self.status == "published" and self.published_at <= utcnow()

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
