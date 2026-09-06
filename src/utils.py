import re
from datetime import datetime, timezone

import bleach

ALLOWED_TAGS = [
    "p", "br", "strong", "em", "b", "i", "u", "s", "a", "ul", "ol", "li", "blockquote",
    "h2", "h3", "h4", "h5", "figure", "figcaption", "img", "hr", "pre", "code",
    "table", "thead", "tbody", "tr", "th", "td", "iframe", "span", "div", "sup", "sub",
]
ALLOWED_ATTRS = {
    "a": ["href", "title", "rel", "target"],
    "img": ["src", "alt", "title", "width", "height", "loading", "srcset", "sizes"],
    "iframe": ["src", "width", "height", "allow", "allowfullscreen", "frameborder", "title"],
    "*": ["class", "id"],
}


def sanitize_html(html: str) -> str:
    return bleach.clean(html or "", tags=ALLOWED_TAGS, attributes=ALLOWED_ATTRS, strip=True)


def slugify(text: str) -> str:
    text = (text or "").lower()
    text = re.sub(r"[’'\"“”]", "", text)
    text = re.sub(r"[^a-z0-9]+", "-", text).strip("-")
    return text[:180] or "post"


def strip_tags(html: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", html or "")).strip()


def make_excerpt(html: str, length: int = 180) -> str:
    text = strip_tags(html)
    if len(text) <= length:
        return text
    cut = text[:length].rsplit(" ", 1)[0]
    return cut + "…"


def format_date(value: datetime) -> str:
    return value.strftime("%B %-d, %Y") if value else ""


def format_datetime(value: datetime) -> str:
    return value.strftime("%B %-d, %Y · %-I:%M %p ET") if value else ""


def time_ago(value: datetime) -> str:
    if not value:
        return ""
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    delta = now - value
    s = int(delta.total_seconds())
    if s < 60:
        return "just now"
    if s < 3600:
        return f"{s // 60}m ago"
    if s < 86400:
        return f"{s // 3600}h ago"
    if s < 86400 * 7:
        return f"{s // 86400}d ago"
    return format_date(value)


EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]{2,}$")


def valid_email(email: str) -> bool:
    return bool(email) and len(email) <= 254 and bool(EMAIL_RE.match(email))
