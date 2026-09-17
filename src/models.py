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
    contact_id = db.Column(db.String(36), default="", index=True)  # BigMailer contact id from the email link (*|_ID|*)
    created_at = db.Column(db.DateTime, default=utcnow)


# post-vote qualifier: "Roughly how much do you have saved for retirement?" (key, label, lowest dollar amount)
SAVINGS_BANDS = [
    ("lt20k", "Under $20,000", 0),
    ("20k", "$20,000 – $50,000", 20_000),
    ("50k", "$50,000 – $250,000", 50_000),
    ("250k", "Over $250,000", 250_000),
    ("na", "Prefer not to say", None),
]
SAVINGS_BAND_KEYS = {k for k, _, _ in SAVINGS_BANDS}
OFFER_AUDIENCES = {
    "all": "Everyone",
    "not_low": "Everyone except under $20k",
    "20k": "Only readers who said $20k+",
    "50k": "Only readers who said $50k+",
}


def offer_visible(audience: str, band: str) -> bool:
    """band is the reader's SAVINGS_BANDS key, or "" if they haven't answered."""
    floor = next((amt for k, _, amt in SAVINGS_BANDS if k == band), None)
    if audience == "not_low":
        return band != "lt20k"
    if audience == "20k":
        return floor is not None and floor >= 20_000
    if audience == "50k":
        return floor is not None and floor >= 50_000
    return True


class QualifierAnswer(db.Model):
    """One reader's answer to the post-vote savings question, keyed by a first-party browser cookie."""

    __tablename__ = "qualifier_answers"
    id = db.Column(db.Integer, primary_key=True)
    rid = db.Column(db.String(32), nullable=False, index=True)  # random id in the "rid" cookie
    contact_id = db.Column(db.String(36), default="", index=True)  # BigMailer contact id, when the reader came from an email
    voter = db.Column(db.String(64), default="")
    poll_id = db.Column(db.Integer, nullable=True)
    question = db.Column(db.String(20), default="savings", index=True)
    answer = db.Column(db.String(8), nullable=False, index=True)
    created_at = db.Column(db.DateTime, default=utcnow, index=True)
    updated_at = db.Column(db.DateTime, default=utcnow, onupdate=utcnow)


class Offer(db.Model):
    """Sponsored offer card shown under poll results; clicks go through /go/<id>/ so they can be counted."""

    __tablename__ = "offers"
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120), nullable=False)  # internal, e.g. "Goldco IRA kit - CPL"
    label = db.Column(db.String(40), default="Sponsored")
    headline = db.Column(db.String(200), nullable=False)
    blurb = db.Column(db.String(300), default="")
    image_url = db.Column(db.String(600), default="")
    cta = db.Column(db.String(40), default="Learn more")
    url = db.Column(db.String(1000), nullable=False)  # may contain {subid}, replaced with pda-o<offer>-p<poll>-c<click>
    active = db.Column(db.Boolean, default=True, index=True)
    weight = db.Column(db.Integer, default=0)  # higher shows first
    audience = db.Column(db.String(16), default="all")  # key of OFFER_AUDIENCES: who sees this card, by savings answer
    impressions = db.Column(db.Integer, default=0)  # human page views that showed this card
    created_at = db.Column(db.DateTime, default=utcnow)
    clicks = db.relationship("OfferClick", back_populates="offer", lazy="dynamic", cascade="all, delete-orphan")


class OfferClick(db.Model):
    __tablename__ = "offer_clicks"
    id = db.Column(db.Integer, primary_key=True)
    offer_id = db.Column(db.Integer, db.ForeignKey("offers.id"), nullable=False, index=True)
    offer = db.relationship("Offer", back_populates="clicks")
    poll_id = db.Column(db.Integer, nullable=True, index=True)
    voter = db.Column(db.String(64), default="")
    user_agent = db.Column(db.String(300), default="")
    is_bot = db.Column(db.Boolean, default=False, index=True)  # link scanners / crawlers: kept but excluded from stats
    segment = db.Column(db.String(8), default="")  # reader's savings band at click time ("" = not answered)
    created_at = db.Column(db.DateTime, default=utcnow, index=True)


class Comment(db.Model):
    """Reader comment on a story. Held as "pending" until approved in the newsroom (unless COMMENTS_AUTO_APPROVE)."""

    __tablename__ = "comments"
    id = db.Column(db.Integer, primary_key=True)
    post_id = db.Column(db.Integer, db.ForeignKey("posts.id"), nullable=False, index=True)
    post = db.relationship("Post", backref=db.backref("comments", lazy="dynamic", cascade="all, delete-orphan"))
    name = db.Column(db.String(60), nullable=False)
    email = db.Column(db.String(254), default="")  # never shown publicly
    body = db.Column(db.Text, nullable=False)
    ip_hash = db.Column(db.String(64), default="", index=True)
    user_agent = db.Column(db.String(300), default="")
    status = db.Column(db.String(16), default="pending", index=True)  # pending | approved | spam
    created_at = db.Column(db.DateTime, default=utcnow, index=True)
