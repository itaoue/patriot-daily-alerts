"""Import every post and page from the live WordPress site via its public REST API.

Usage (locally, or as a one-off Railway command):
    python tools/import_wp.py                # import everything (2,000+ posts, ~5 min)
    python tools/import_wp.py --max 300      # only the newest 300 posts
    python tools/import_wp.py --source https://patriotdailyalerts.com

The same importer is available as a button in the admin at /admin/import.
Safe to re-run: posts are matched by their WordPress ID and updated in place.
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.importer import import_pages, import_posts  # noqa: E402
from src.main import app  # noqa: E402

if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--source", default="https://patriotdailyalerts.com")
    ap.add_argument("--max", type=int, default=0, help="stop after N newest posts (0 = all)")
    ap.add_argument("--skip-pages", action="store_true")
    args = ap.parse_args()
    source = args.source.rstrip("/")
    with app.app_context():
        print(f"Importing from {source} …")
        print(f"Imported/updated {import_posts(source, args.max, log=lambda m: print('  ' + m, flush=True))} posts")
        if not args.skip_pages:
            print(f"Imported/updated {import_pages(source)} pages")
