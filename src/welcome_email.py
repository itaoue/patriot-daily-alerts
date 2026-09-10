"""The welcome email, sent the moment a reader subscribes.

The first minutes after signup are when a reader is most likely to open, click and move the message to their Primary
inbox, and that early engagement is what decides where every later issue lands. So this goes out immediately rather
than waiting for the next scheduled newsletter, and it is deliberately plain: one small logo, then text. The single
ask is the inbox move; today's headlines and poll are there to earn a click, which is itself a positive signal.

Copy lives in content/welcome_email.json. The body is built inside the request (it reads posts and polls) and handed
to notify.send_to as finished strings, so the sending thread never touches the database.
"""
import html as _html
import json
import logging
import os

from flask import current_app
from itsdangerous import BadSignature, URLSafeSerializer

from src import notify

log = logging.getLogger(__name__)

FONT = "Helvetica,Arial,sans-serif"
NAVY, RED, INK, GREY, RULE = "#0C1F3D", "#B3202E", "#000000", "#666666", "#dddddd"
UTM = "utm_source=welcome&utm_medium=email&utm_campaign=welcome"


def config() -> dict:
    path = os.path.join(os.path.dirname(__file__), "..", "content", "welcome_email.json")
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError) as exc:
        log.warning("welcome_email.json unreadable: %s", exc)
        return {}


def enabled() -> bool:
    return bool(config().get("enabled", True)) and notify.transport_configured()


# ---- one-click unsubscribe -------------------------------------------------------------------

def unsubscribe_token(email: str) -> str:
    return URLSafeSerializer(current_app.config["SECRET_KEY"], salt="list-unsubscribe").dumps(email)


def verify_unsubscribe_token(token: str):
    try:
        return URLSafeSerializer(current_app.config["SECRET_KEY"], salt="list-unsubscribe").loads(token)
    except BadSignature:
        return None


def _esc(s) -> str:
    return _html.escape(str(s or ""))


def _link(url: str) -> str:
    return f"{url}{'&' if '?' in url else '?'}{UTM}"


def build(email: str) -> dict:
    """Subject, text and HTML for one reader, plus the List-Unsubscribe headers."""
    from src.models import Poll, Post, utcnow

    cfg = config()
    site = current_app.config["SITE_URL"]
    site_name = current_app.config["SITE_NAME"]
    unsub = f"{site}/remove-from-our-email-list/{unsubscribe_token(email)}/"

    posts = (
        Post.query.filter(Post.status == "published", Post.published_at <= utcnow())
        .order_by(Post.published_at.desc()).limit(int(cfg.get("stories") or 3)).all()
    )
    poll = Poll.query.order_by(Poll.created_at.desc()).first()

    # ---- plain text ----
    lines = list(cfg.get("intro") or [])
    lines += ["", cfg.get("ask_heading", ""), "", cfg.get("ask_intro", "")]
    lines += [f"  * {label} {body}" for label, body in cfg.get("ask_steps") or []]
    lines += ["", cfg.get("ask_outro", "")]
    if posts:
        lines += ["", f"{cfg.get('stories_heading', '')}:", ""]
        lines += [f"  {p.title}\n  {_link(site + p.url)}" for p in posts]
    if poll:
        lines += ["", f"{cfg.get('poll_heading', '')}:", "", f"  {poll.question}"]
        lines += [f"  {label}: {_link(f'{site}/poll/{poll.id}/vote/{i}/')}" for i, label in enumerate(poll.option_list)]
    lines += ["", cfg.get("reply_line", ""), "", cfg.get("signoff", ""), "",
              f"Unsubscribe: {unsub}", current_app.config.get("POSTAL_ADDRESS", "")]
    text = "\n".join(lines).strip() + "\n"

    # ---- html ----
    p_style = f"margin:0 0 16px;font-family:{FONT};font-size:16px;line-height:160%;color:{INK};"
    body = "".join(f'<p style="{p_style}">{_esc(t)}</p>' for t in cfg.get("intro") or [])
    body += (f'<p style="{p_style}font-weight:700;">{_esc(cfg.get("ask_heading"))}</p>'
             f'<p style="{p_style}">{_esc(cfg.get("ask_intro"))}</p><ul style="margin:0 0 16px;padding-left:22px;">')
    body += "".join(
        f'<li style="font-family:{FONT};font-size:16px;line-height:160%;color:{INK};margin-bottom:8px;">'
        f"<b>{_esc(label)}</b> {_esc(step)}</li>" for label, step in cfg.get("ask_steps") or []
    )
    body += f'</ul><p style="{p_style}">{_esc(cfg.get("ask_outro"))}</p>'

    if posts:
        body += _heading(cfg.get("stories_heading"))
        for p in posts:
            body += (f'<p style="margin:0 0 12px;padding-bottom:12px;border-bottom:1px solid {RULE};">'
                     f'<a href="{_esc(_link(site + p.url))}" style="font-family:{FONT};font-size:17px;line-height:135%;'
                     f'font-weight:700;color:{NAVY};text-decoration:none;">{_esc(p.title)}</a></p>')
    if poll:
        body += _heading(cfg.get("poll_heading"))
        body += f'<p style="{p_style}font-weight:700;">{_esc(poll.question)}</p>'
        for i, label in enumerate(poll.option_list):
            body += (f'<p style="margin:0 0 8px;"><a href="{_esc(_link(f"{site}/poll/{poll.id}/vote/{i}/"))}" '
                     f'style="display:inline-block;font-family:{FONT};font-size:16px;font-weight:700;color:{NAVY};'
                     f'text-decoration:none;border:2px solid {NAVY};border-radius:3px;padding:9px 16px;">{_esc(label)}</a></p>')

    body += (f'<p style="{p_style}margin-top:24px;">{_esc(cfg.get("reply_line"))}</p>'
             f'<p style="{p_style}">{_esc(cfg.get("signoff"))}</p>')

    html = f"""<!DOCTYPE html>
<html lang="en" xmlns="http://www.w3.org/1999/xhtml">
<head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="x-apple-disable-message-reformatting"><title>{_esc(cfg.get("subject"))}</title></head>
<body style="margin:0;padding:0;background:#ffffff;">
<div style="display:none;font-size:1px;line-height:1px;max-height:0;max-width:0;opacity:0;overflow:hidden;">
{_esc(cfg.get("preheader"))}</div>
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" style="background:#ffffff;">
<tr><td align="center" style="padding:24px 16px;">
<table role="presentation" class="wrap" width="600" cellpadding="0" cellspacing="0" border="0" style="width:600px;max-width:100%;">
  <tr><td style="padding:0 0 22px;">
    <a href="{_esc(_link(site + '/'))}"><img src="{site}/static/img/logo.png" width="260" alt="{_esc(site_name)}"
      style="display:block;width:260px;max-width:100%;height:auto;border:0;"></a>
  </td></tr>
  <tr><td>{body}</td></tr>
  <tr><td style="padding:24px 0 0;border-top:1px solid {RULE};font-family:{FONT};font-size:12px;line-height:160%;color:{GREY};">
    You are receiving this because you subscribed at {_esc(site_name)}.
    <a href="{_esc(unsub)}" style="color:{GREY};text-decoration:underline;">Unsubscribe</a> &middot;
    <a href="{site}/privacy-policy/" style="color:{GREY};text-decoration:underline;">Privacy Policy</a><br>
    {_esc(current_app.config.get("POSTAL_ADDRESS", ""))}
  </td></tr>
</table>
</td></tr></table>
</body></html>"""

    return {
        "subject": cfg.get("subject") or f"Welcome to {site_name}",
        "text": text,
        "html": html,
        "headers": {"List-Unsubscribe": f"<{unsub}>", "List-Unsubscribe-Post": "List-Unsubscribe=One-Click"},
    }


def send(email: str, wait: bool = False) -> bool:
    """Send the welcome email to one new subscriber. Returns False when disabled or no transport is configured."""
    if not enabled():
        return False
    msg = build(email)
    return notify.send_to([email], msg["subject"], msg["text"], msg["html"], headers=msg["headers"], wait=wait)


def _heading(label) -> str:
    return (f'<p style="margin:28px 0 14px;font-family:{FONT};font-size:15px;font-weight:700;letter-spacing:.04em;'
            f'text-transform:uppercase;color:{INK};border-bottom:3px solid {RED};padding-bottom:6px;">{_esc(label)}</p>')
