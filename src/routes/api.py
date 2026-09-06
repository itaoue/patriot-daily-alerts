import logging

import requests
from flask import Blueprint, current_app, jsonify, redirect, render_template, request

from src.models import ContactMessage, Subscriber, db, utcnow
from src.utils import valid_email

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


@api_bp.route("/subscribe", methods=["POST"])
def subscribe():
    data = request.get_json(silent=True) or request.form
    email = (data.get("email") or "").strip().lower()
    if data.get("website"):  # honeypot field filled by bots
        return jsonify(ok=True)
    if not valid_email(email):
        if _wants_json():
            return jsonify(ok=False, error="Please enter a valid email address."), 400
        return render_template("subscribed.html", ok=False, email=email), 400
    sub = Subscriber.query.filter_by(email=email).first()
    if sub:
        sub.unsubscribed_at = None
    else:
        db.session.add(Subscriber(email=email, source=(data.get("source") or "site")[:80]))
    db.session.commit()
    _push_bigmailer(email)
    if _wants_json():
        return jsonify(ok=True)
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
    db.session.add(ContactMessage(name=name, email=email, subject=subject, message=message))
    db.session.commit()
    return redirect("/contact-us/?sent=1")
