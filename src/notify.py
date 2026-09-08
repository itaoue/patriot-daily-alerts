"""Email notifications to the editor (new comments, contact messages).

Transport is chosen from the environment: RESEND_API_KEY (HTTPS, simplest on Railway) or SMTP_HOST/SMTP_USER/SMTP_PASSWORD
(e.g. Gmail with an app password). NOTIFY_EMAIL is the recipient; nothing is sent when it is unset. Sending runs in a
background thread so requests never wait on the mail server.
"""
import logging
import smtplib
import threading
from email.message import EmailMessage
from email.utils import formataddr

import requests
from flask import current_app
from itsdangerous import BadSignature, URLSafeSerializer

log = logging.getLogger(__name__)


def configured(app=None) -> bool:
    cfg = (app or current_app).config
    return bool(cfg.get("NOTIFY_EMAIL")) and bool(cfg.get("RESEND_API_KEY") or (cfg.get("SMTP_HOST") and cfg.get("SMTP_USER")))


def _send_now(cfg: dict, subject: str, text: str, html: str) -> None:
    to = [a.strip() for a in cfg["NOTIFY_EMAIL"].split(",") if a.strip()]
    sender = cfg.get("MAIL_FROM") or cfg.get("SMTP_USER") or "news@patriotdailyalerts.com"
    if cfg.get("RESEND_API_KEY"):
        r = requests.post("https://api.resend.com/emails", timeout=20,
                          headers={"Authorization": f"Bearer {cfg['RESEND_API_KEY']}", "Content-Type": "application/json"},
                          json={"from": formataddr((cfg["SITE_NAME"], sender)), "to": to, "subject": subject, "text": text, "html": html})
        r.raise_for_status()
        return
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = formataddr((cfg["SITE_NAME"], sender))
    msg["To"] = ", ".join(to)
    msg.set_content(text)
    msg.add_alternative(html, subtype="html")
    port = int(cfg.get("SMTP_PORT") or 587)
    if port == 465:
        with smtplib.SMTP_SSL(cfg["SMTP_HOST"], port, timeout=20) as s:
            s.login(cfg["SMTP_USER"], cfg["SMTP_PASSWORD"])
            s.send_message(msg)
    else:
        with smtplib.SMTP(cfg["SMTP_HOST"], port, timeout=20) as s:
            s.starttls()
            s.login(cfg["SMTP_USER"], cfg["SMTP_PASSWORD"])
            s.send_message(msg)


def send(subject: str, text: str, html: str, wait: bool = False) -> bool:
    """Queue an email to the editor. Returns False when notifications are not configured."""
    if not configured():
        return False
    cfg = {k: current_app.config.get(k) for k in ("NOTIFY_EMAIL", "MAIL_FROM", "RESEND_API_KEY", "SMTP_HOST", "SMTP_PORT",
                                                    "SMTP_USER", "SMTP_PASSWORD", "SITE_NAME")}

    def run():
        try:
            _send_now(cfg, subject, text, html)
        except Exception as exc:  # noqa: BLE001 - notifications must never break a request
            log.warning("notification email failed: %s", exc)
            if wait:
                raise

    if wait or current_app.config.get("TESTING"):
        run()
    else:
        threading.Thread(target=run, daemon=True).start()
    return True


def moderation_token(comment_id: int, action: str) -> str:
    return URLSafeSerializer(current_app.config["SECRET_KEY"], salt="comment-moderation").dumps([comment_id, action])


def verify_moderation_token(token: str):
    try:
        return URLSafeSerializer(current_app.config["SECRET_KEY"], salt="comment-moderation").loads(token)
    except BadSignature:
        return None


def notify_comment(comment) -> bool:
    from flask import url_for

    site = current_app.config["SITE_URL"]
    approve = site + url_for("admin.moderate_by_token", token=moderation_token(comment.id, "approved"))
    spam = site + url_for("admin.moderate_by_token", token=moderation_token(comment.id, "spam"))
    queue = site + url_for("admin.comments")
    story = site + comment.post.url
    subject = f"New comment on: {comment.post.title[:80]}"
    text = (f"{comment.name} commented on \"{comment.post.title}\":\n\n{comment.body}\n\n"
            f"Approve: {approve}\nMark spam: {spam}\nReview queue: {queue}\nStory: {story}\n")
    btn = "color:#fff;padding:10px 16px;text-decoration:none;border-radius:4px"
    html = (f'<p><b>{_esc(comment.name)}</b> commented on <a href="{story}">{_esc(comment.post.title)}</a>:</p>'
            f'<blockquote style="border-left:3px solid #B3202E;padding:6px 12px;margin:12px 0;white-space:pre-wrap">'
            f'{_esc(comment.body)}</blockquote>'
            f'<p><a href="{approve}" style="background:#1c6b32;{btn}">Approve</a> &nbsp; '
            f'<a href="{spam}" style="background:#B3202E;{btn}">Mark spam</a> &nbsp; '
            f'<a href="{queue}">Open the review queue</a></p>')
    return send(subject, text, html)


def notify_contact(msg) -> bool:
    site = current_app.config["SITE_URL"]
    subject = f"Contact form: {msg.subject or '(no subject)'}"[:120]
    text = f"From: {msg.name} <{msg.email}>\n\n{msg.message}\n\nAll messages: {site}/admin/messages/\n"
    html = (f"<p><b>From:</b> {_esc(msg.name)} &lt;{_esc(msg.email)}&gt;</p><p style='white-space:pre-wrap'>{_esc(msg.message)}</p>"
            f"<p><a href='{site}/admin/messages/'>All messages</a></p>")
    return send(subject, text, html)


def _esc(s: str) -> str:
    import html

    return html.escape(s or "")
