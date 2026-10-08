"""Everflow publisher API: record conversions and tie them back to our offer clicks.

Conversions arrive two ways and land in the same ``offer_conversions`` row, keyed by Everflow's transaction id:

* postback (``/api/everflow/postback``), fired by Everflow the moment a lead converts;
* the reporting API (``sync``), run once a day by the in-app scheduler and on demand through
  ``POST /api/everflow/sync``. It fills in anything a postback missed and picks up later status
  changes (a $90 CPA lead can be rejected days after the click).

The affiliate report has no status field and calls our payout ``revenue``. A conversion that later
drops out of the report (rejected or reversed by the advertiser) is marked ``not_reported`` and leaves
the revenue; it is counted again if it comes back. Event conversions share the base conversion's
transaction id, so they are stored as ``<transaction id>:<conversion id>``.

Site links carry ``sub1=pda-web&sub2=o<offer>&sub3=a<post>|p<poll>&sub4=c<click>&sub5=<savings band>``;
the newsletter's carry ``sub1=pda``. Links that use ``{subid}`` send everything in sub1
(``pda-o3-p0-c45-snone``), and those match too. The Everflow affiliate account is shared with the sister
site, Patriot Daily Wire, so the report also lists its conversions (``sub1=pdw…``): they are kept as
source "other" and never matched to our clicks, whose ids overlap with PDW's.
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import re
from datetime import datetime, timedelta, timezone

import requests

from src.models import OfferClick, OfferConversion, db, utcnow

log = logging.getLogger(__name__)

# statuses that count as money; anything else (rejected, invalid, ...) is kept but left out of revenue
COUNTED_STATUSES = {"approved", "pending"}
GRACE = timedelta(days=1)  # a fresh postback may not be in the report yet
PAGE_SIZE = 500
_CLICK_RE = re.compile(r"(?:^|-)c(\d+)(?:-|$)")


def postback_key(config) -> str:
    """Secret that the postback URL must carry; EVERFLOW_POSTBACK_KEY, or derived from SECRET_KEY."""
    if config.get("EVERFLOW_POSTBACK_KEY"):
        return config["EVERFLOW_POSTBACK_KEY"]
    return hmac.new(config["SECRET_KEY"].encode(), b"everflow-postback", hashlib.sha256).hexdigest()[:32]


def postback_url(config) -> str:
    """The global postback to paste into Everflow (its tokens in braces are filled in by Everflow)."""
    return (f"{config['SITE_URL']}/api/everflow/postback?key={postback_key(config)}"
            "&tid={transaction_id}&sub1={sub1}&sub2={sub2}&sub3={sub3}&sub4={sub4}&sub5={sub5}"
            "&payout={payout_amount}&offer={offer_id}")


def source_of(sub1: str) -> str:
    sub1 = (sub1 or "").lower()
    if sub1.startswith(("pda-web", "pda-o")):
        return "web"
    if sub1 == "pda" or sub1.startswith("pda-"):
        return "email"
    return "other"


def click_id_of(subs: list) -> int | None:
    """Our click id from sub4 ("c456"), or from a {subid}-style sub1 ("pda-o3-p12-c456-s50k")."""
    for value in (subs[3] if len(subs) > 3 else "", subs[0] if subs else ""):
        m = _CLICK_RE.search(value or "")
        if m:
            return int(m.group(1))
    return None


def _money(value) -> float:
    try:
        return round(float(value), 2)
    except (TypeError, ValueError):
        return 0.0  # e.g. a literal "{payout_amount}" when the token is not available


def _clean(value) -> str:
    value = str(value or "").strip()
    return "" if value.startswith("{") and value.endswith("}") else value  # unfilled Everflow token


def record(transaction_id, subs, payout=None, status=None, network_offer_id="", network_offer_name="",
           converted_at=None) -> tuple:
    """Create or update one conversion. ``None`` leaves a stored value alone. Returns (row, created); no commit."""
    transaction_id = _clean(transaction_id)[:64]
    if not transaction_id:
        return None, False
    subs = [_clean(s)[:120] for s in (list(subs) + [""] * 5)[:5]]
    row = OfferConversion.query.filter_by(transaction_id=transaction_id).first()
    created = row is None
    if created:
        row = OfferConversion(transaction_id=transaction_id, status="pending", payout=0.0)
        db.session.add(row)
    if any(subs):
        row.subs = "|".join(subs)
        row.source = source_of(subs[0])
        click = db.session.get(OfferClick, click_id_of(subs)) if click_id_of(subs) else None
        if click and row.source == "web":
            row.click_id, row.offer_id, row.post_id = click.id, click.offer_id, click.post_id
    if payout is not None:
        row.payout = _money(payout)
    if status:
        row.status = str(status).strip().lower()[:16]
    if network_offer_id:
        row.network_offer_id = str(network_offer_id)[:32]
    if network_offer_name:
        row.network_offer_name = str(network_offer_name)[:200]
    if converted_at:
        row.converted_at = converted_at
    return row, created


def _from_report(c: dict) -> dict:
    """Map one conversion from Everflow's affiliate conversions report to ``record`` arguments."""
    offer = ((c.get("relationship") or {}).get("offer") or {})
    ts = c.get("conversion_unix_timestamp")
    tid = c.get("transaction_id") or c.get("conversion_id")
    if c.get("is_event") and c.get("conversion_id"):
        tid = f"{tid}:{c['conversion_id']}"
    return {
        "transaction_id": tid,
        "subs": [c.get(f"sub{i}") for i in range(1, 6)],
        "payout": c["payout"] if c.get("payout") is not None else c.get("revenue"),
        "status": c.get("status") or "approved",
        "network_offer_id": offer.get("network_offer_id") or c.get("network_offer_id") or "",
        "network_offer_name": offer.get("name") or "",
        "converted_at": datetime.fromtimestamp(int(ts), timezone.utc).replace(tzinfo=None) if ts else None,
    }


def api_keys(config) -> list:
    """EVERFLOW_API_KEY may list several keys, comma-separated: one per network (each network has its own portal)."""
    return [k.strip() for k in (config.get("EVERFLOW_API_KEY") or "").split(",") if k.strip()]


def fetch_conversions(config, start, end) -> list:
    """All conversions between two dates (inclusive), every page, from every network key."""
    out = []
    for key in api_keys(config):
        out.extend(_fetch_one(config, key, start, end))
    return out


def _fetch_one(config, key, start, end) -> list:
    out, page = [], 1
    while True:
        r = requests.post(
            f"{config['EVERFLOW_API_URL']}/v1/affiliates/reporting/conversions",
            params={"page": page, "page_size": PAGE_SIZE}, timeout=60,
            headers={"X-Eflow-API-Key": key, "Content-Type": "application/json"},
            json={"from": start.isoformat(), "to": end.isoformat(), "timezone_id": config["EVERFLOW_TIMEZONE_ID"],
                  "show_conversions": True, "show_events": True, "query": {"filters": []}},
        )
        r.raise_for_status()
        data = r.json()
        rows = data.get("conversions") or []
        out.extend(rows)
        total = int((data.get("paging") or {}).get("total_count") or 0)
        if not rows or len(rows) < PAGE_SIZE or len(out) >= total:
            return out
        page += 1


def sync(config, days: int = 30) -> dict:
    """Pull the last `days` days of conversions and upsert them. Call inside an app context."""
    if not api_keys(config):
        return {"ok": False, "error": "EVERFLOW_API_KEY is not set"}
    end = utcnow().date() + timedelta(days=1)  # Everflow reports in its own timezone; a day of slack covers the gap
    start = end - timedelta(days=days + 1)
    rows = fetch_conversions(config, start, end)
    created = matched = 0
    seen, by_source, by_offer, sub1_prefixes = set(), {}, {}, {}
    for c in rows:
        row, new = record(**_from_report(c))
        if row is None:
            continue
        seen.add(row.transaction_id)
        created += new
        matched += bool(row.click_id)
        prefix = (row.subs.split("|")[0].split("-")[0] or "(empty)")[:20] if row.source == "other" else row.source
        sub1_prefixes[prefix] = sub1_prefixes.get(prefix, 0) + 1
        for bucket, key in ((by_source, row.source), (by_offer, row.network_offer_name or row.network_offer_id or "?")):
            agg = bucket.setdefault(key, {"n": 0, "revenue": 0.0})
            agg["n"] += 1
            agg["revenue"] = round(agg["revenue"] + (row.payout or 0), 2)
    # gone from the report: rejected or reversed. Only inside the window, and not ones too new to be listed
    window_start = datetime.combine(start + timedelta(days=1), datetime.min.time())
    dropped = 0
    for row in OfferConversion.query.filter(OfferConversion.converted_at >= window_start,
                                            OfferConversion.converted_at < utcnow() - GRACE).all():
        if row.transaction_id not in seen and row.status != "not_reported":
            row.status = "not_reported"
            dropped += 1
    db.session.commit()
    return {"ok": True, "from": start.isoformat(), "to": end.isoformat(), "conversions": len(rows), "created": created,
            "matched_to_clicks": matched, "not_reported": dropped, "by_source": by_source, "by_offer": by_offer,
            "sub1_prefixes": sub1_prefixes, "networks": len(api_keys(config))}
