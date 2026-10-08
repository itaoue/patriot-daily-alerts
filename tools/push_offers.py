"""Push the offer cards in content/offers.json to the live site (created or updated in place, matched by name).

    export PUBLISH_TOKEN=...            # same value as the Railway variable (read it with `read -rs`)
    python tools/push_offers.py                         # content/offers.json
    python tools/push_offers.py content/offers.json

Click and impression counts are kept on update. SITE_URL defaults to https://patriotdailyalerts.com.
"""
import argparse
import json
import os
import sys

import requests

ap = argparse.ArgumentParser()
ap.add_argument("file", nargs="?", default="content/offers.json")
args = ap.parse_args()
site = os.environ.get("SITE_URL", "https://patriotdailyalerts.com").rstrip("/")
token = os.environ.get("PUBLISH_TOKEN", "")
if not token:
    sys.exit("PUBLISH_TOKEN is not set")
with open(args.file, encoding="utf-8") as fh:
    offers = json.load(fh)["offers"]
r = requests.post(f"{site}/api/offers", json={"offers": offers}, headers={"Authorization": f"Bearer {token}"}, timeout=60)
if r.status_code != 200:
    sys.exit(f"{r.status_code} {r.text[:300]}")
for o in r.json()["offers"]:
    print("created" if o["created"] else "updated", o["id"], o["name"], "" if o["active"] else "(paused)")
