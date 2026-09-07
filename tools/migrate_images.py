"""Copy the images our stories reference from the old WordPress host into this repo.

Downloads every wp-content/uploads file used as a featured image or inside the body of
the newest N stories (same selection as the importer) into src/static/uploads/, keeping
the original yyyy/mm/filename path. Flask serves that folder at /wp-content/uploads/...,
so the existing absolute URLs keep working once the domain points at the new site.

    python tools/migrate_images.py --max 300      # newest 300 stories (default)
    python tools/migrate_images.py --max 0        # everything (~750 MB, not recommended in git)
    python tools/migrate_images.py --from-db      # use the stories in the local DATABASE_URL instead

Re-running only fetches files that are missing. Commit src/static/uploads afterwards.
"""
import argparse
import concurrent.futures as cf
import os
import re
import sys
from urllib.parse import unquote, urlsplit

import requests

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.importer import UA, fetch  # noqa: E402

UPLOAD_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src", "static", "uploads")
URL_RE = re.compile(r"https?://(?:www\.)?patriotdailyalerts\.com/wp-content/uploads/([^\s\"'<>)]+)")


def urls_in(text: str) -> set:
    return {m.group(0).rstrip(",;") for m in URL_RE.finditer(text or "")}


def collect_from_api(source: str, max_posts: int) -> set:
    urls, page, seen = set(), 1, 0
    while True:
        r = fetch(f"{source}/wp-json/wp/v2/posts", {"per_page": 50, "page": page, "_embed": 1})
        if r is None:
            break
        items = r.json()
        if not items:
            break
        for p in items:
            media = (p.get("_embedded", {}).get("wp:featuredmedia") or [{}])[0]
            if media.get("source_url"):
                urls.add(media["source_url"])
            urls |= urls_in(p["content"]["rendered"])
            seen += 1
            if max_posts and seen >= max_posts:
                return urls
        if page >= int(r.headers.get("X-WP-TotalPages", page)):
            break
        page += 1
        print(f"  scanned {seen} stories, {len(urls)} images so far", flush=True)
    r = fetch(f"{source}/wp-json/wp/v2/pages", {"per_page": 50})
    for pg in r.json() if r else []:
        urls |= urls_in(pg["content"]["rendered"])
    return urls


def collect_from_db() -> set:
    from src.main import app
    from src.models import Page, Post

    urls = set()
    with app.app_context():
        for p in Post.query.all():
            urls |= urls_in(p.image_url) | urls_in(p.body_html)
        for pg in Page.query.all():
            urls |= urls_in(pg.body_html)
    return urls


def local_path(url: str) -> str:
    rel = unquote(urlsplit(url).path.split("/wp-content/uploads/", 1)[1])
    rel = os.path.normpath(rel)
    if rel.startswith("..") or os.path.isabs(rel):
        raise ValueError(f"unsafe path in {url}")
    return os.path.join(UPLOAD_DIR, rel)


def download(url: str) -> tuple:
    path = local_path(url)
    if os.path.exists(path):
        return url, "exists", os.path.getsize(path)
    try:
        r = requests.get(url, headers=UA, timeout=60)
        if r.status_code != 200 or not r.content:
            return url, f"http {r.status_code}", 0
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "wb") as fh:
            fh.write(r.content)
        return url, "ok", len(r.content)
    except requests.RequestException as exc:
        return url, f"error {exc}", 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--source", default="https://patriotdailyalerts.com")
    ap.add_argument("--max", type=int, default=300, help="newest N stories (0 = all)")
    ap.add_argument("--from-db", action="store_true", help="collect URLs from the local database instead of the WP API")
    args = ap.parse_args()

    urls = collect_from_db() if args.from_db else collect_from_api(args.source.rstrip("/"), args.max)
    print(f"{len(urls)} image URLs to mirror into {UPLOAD_DIR}")
    with cf.ThreadPoolExecutor(8) as ex:
        results = list(ex.map(download, sorted(urls)))
    ok = [r for r in results if r[1] == "ok"]
    skipped = [r for r in results if r[1] == "exists"]
    failed = [r for r in results if r[1] not in ("ok", "exists")]
    print(f"downloaded {len(ok)} ({sum(r[2] for r in ok) / 1e6:.1f} MB), already present {len(skipped)}, failed {len(failed)}")
    for u, why, _ in failed:
        print("  FAILED", why, u)
