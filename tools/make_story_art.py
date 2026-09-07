#!/usr/bin/env python3
"""
Generate a photorealistic editorial image for a story with the xAI image API (same setup as RetireBrief).

    export XAI_API_KEY=xai-...
    python tools/make_story_art.py --scene "Empty Senate chamber at dusk, papers on a desk" --out src/static/uploads/2026/09/slug.jpg

Output is a 1200x630 JPEG. No faces, no text, no logos.
"""
import argparse
import base64
import json
import os
import pathlib
import sys
import time
import urllib.error
import urllib.request

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from imgfit import fit  # noqa: E402

API = "https://api.x.ai/v1/images/generations"
DEFAULT_MODEL = "grok-imagine-image-quality"
STYLE = (
    "Photorealistic editorial news photograph, shot on a full-frame DSLR with a 50mm lens, natural light, "
    "realistic colors, shallow depth of field, no text overlays, no watermark, no logos, no visible faces "
    "(hands, backs, silhouettes or crowds from behind are fine), wide horizontal composition suitable for a "
    "news website header. Scene: "
)


def generate(scene, out_path, model=DEFAULT_MODEL, key=None):
    key = key or os.environ.get("XAI_API_KEY")
    if not key:
        raise RuntimeError("XAI_API_KEY is not set")
    req = urllib.request.Request(
        API,
        data=json.dumps({"model": model, "prompt": STYLE + scene, "n": 1, "response_format": "b64_json"}).encode(),
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
    )
    for attempt in range(3):
        try:
            with urllib.request.urlopen(req, timeout=180) as r:
                d = json.load(r)
            data = base64.b64decode(d["data"][0]["b64_json"])
            break
        except urllib.error.HTTPError as e:
            body = e.read().decode(errors="ignore")[:300]
            if e.code in (429, 500, 502, 503) and attempt < 2:
                time.sleep(5 * (attempt + 1))
                continue
            raise RuntimeError(f"xAI error {e.code}: {body}") from e
    out_path = pathlib.Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_bytes(data)
    fit(out_path, 1200, 630, 0.5)
    return out_path


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--scene", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--model", default=DEFAULT_MODEL)
    a = ap.parse_args()
    print(generate(a.scene, a.out, a.model))
