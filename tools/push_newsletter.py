#!/usr/bin/env python3
"""
Push a built newsletter issue to BigMailer as a campaign (draft by default).

    python tools/push_newsletter.py 2026-09-08-am              # create a DRAFT campaign; send/schedule inside BigMailer
    python tools/push_newsletter.py 2026-09-08-am --ready      # mark it ready so BigMailer sends it
    python tools/push_newsletter.py 2026-09-08-am --dry-run
    python tools/push_newsletter.py --brands | --lists         # discover ids

Environment: BIGMAILER_API_KEY, BIGMAILER_BRAND_ID, BIGMAILER_LIST_ID, optional BM_FROM_NAME / BM_FROM_EMAIL / BM_REPLY_TO.
The HTML uses BigMailer merge tags *|UNSUB|* and the static "View online" URL.
"""
import argparse
import os
import pathlib
import sys

import requests

ROOT = pathlib.Path(__file__).resolve().parent.parent
BM = "https://api.bigmailer.io/v1"


def need(name):
    v = os.environ.get(name, "")
    if not v:
        sys.exit(f"Set {name} first.")
    return v


def headers():
    return {"X-API-Key": need("BIGMAILER_API_KEY"), "accept": "application/json", "content-type": "application/json"}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("campaign", nargs="?", help="issue id like 2026-09-08-am (from dist/newsletters)")
    ap.add_argument("--ready", action="store_true", help="mark the campaign ready to send (default: draft)")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--brands", action="store_true")
    ap.add_argument("--lists", action="store_true")
    a = ap.parse_args()
    if a.brands:
        for b in requests.get(f"{BM}/brands?limit=50", headers=headers(), timeout=30).json().get("data", []):
            print(f"  {b['id']}  {b['name']}  from: {b.get('from_name')} <{b.get('from_email')}>")
        return
    brand = need("BIGMAILER_BRAND_ID")
    if a.lists:
        for lst in requests.get(f"{BM}/brands/{brand}/lists?limit=100", headers=headers(), timeout=30).json().get("data", []):
            print(f"  {lst['id']}  {lst['name']}  ({lst.get('num_contacts', '?')} contacts)")
        return
    if not a.campaign:
        sys.exit("campaign id required, e.g. 2026-09-08-am")
    import json
    base = ROOT / "dist" / "newsletters" / a.campaign
    issue = json.loads(base.with_suffix(".json").read_text())
    html = base.with_suffix(".html").read_text(encoding="utf-8")
    text = base.with_suffix(".txt").read_text(encoding="utf-8")
    b = requests.get(f"{BM}/brands/{brand}", headers=headers(), timeout=30).json()
    from_name = os.environ.get("BM_FROM_NAME") or b.get("from_name") or "Patriot Daily Alerts"
    from_email = os.environ.get("BM_FROM_EMAIL") or b.get("from_email")
    payload = {
        "name": f"PDA {a.campaign}", "subject": issue["subject"], "preview": issue.get("preheader", ""),
        "from": {"name": from_name, "email": from_email},
        "reply_to": {"name": from_name, "email": os.environ.get("BM_REPLY_TO") or from_email},
        "html": html, "text": text, "list_ids": [need("BIGMAILER_LIST_ID")],
        "track_opens": True, "track_clicks": True, "ready": bool(a.ready),
    }
    print(f"brand {b.get('name')} | from {from_name} <{from_email}> | subject '{issue['subject']}' | html {len(html):,} chars | ready={a.ready}")
    if a.dry_run:
        print("dry run: nothing created")
        return
    r = requests.post(f"{BM}/brands/{brand}/bulk-campaigns", headers=headers(), json=payload, timeout=60)
    if r.status_code >= 300:
        sys.exit(f"BigMailer error {r.status_code}: {r.text[:400]}")
    c = r.json()
    print(f"campaign created: id {c.get('id')} status {c.get('status')}")


if __name__ == "__main__":
    main()
