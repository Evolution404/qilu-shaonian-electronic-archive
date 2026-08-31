#!/usr/bin/env python3
"""Recover Sina blog media URLs hidden in raw/escaped source for issue-specific posts.

The normal HTML parser misses some legacy Sina body images because URLs can live in
``real_src`` attributes, escaped HTML fragments, or script strings. This targeted pass reads
already-discovered editor posts, prioritizes issue/page-review semantics, extracts sinaimg.cn
URLs directly from raw source, derives only legacy large/orignal sibling variants, and records
reachable image metadata/hash. Image bytes are transient and are not committed.
"""
from __future__ import annotations

import csv
import hashlib
import html
import io
import json
import re
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
POSTS = ROOT / "data" / "repost_fullpage" / "sina_posts.csv"
OUTDIR = ROOT / "data" / "repost_fullpage"
OUT = OUTDIR / "sina_inline_media.csv"
REPORT = OUTDIR / "sina_inline_media_report.json"
UA = "Mozilla/5.0 qilu-shaonian-sina-inline-media/1.0"
TIMEOUT = 14
MAX_HTML = 5 * 1024 * 1024
MAX_IMAGE = 25 * 1024 * 1024
WORKERS = 14

SEMANTIC = re.compile(r"评报|看图说事|版面|合刊|第\s*\d{3,5}\s*期|\d{3,5}\s*期|一版|二版|三版|四版|头版|报纸", re.I)
SINA_URL = re.compile(
    r'''(?i)(?:https?:)?(?:\\?/\\?/|//)(?:s\d+|photo|album|ww\d+|wx\d+)\.sinaimg\.cn(?:\\?/|/)[^"'<>\s\\]+'''
)
# Also catch plain unescaped http(s) URLs with query suffixes.
SINA_HTTP = re.compile(r'''(?i)https?://(?:s\d+|photo|album|ww\d+|wx\d+)\.sinaimg\.cn/[^"'<>\s]+''')
PATH_CLASS = re.compile(r"/(middle|bmiddle|thumbnail|mw\d+|orj\d+|square|large|orignal)/", re.I)


def get(url: str, limit: int, accept="*/*"):
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": accept})
    with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
        body = r.read(limit + 1)
        if len(body) > limit:
            raise ValueError("response too large")
        return body, r.geturl(), {k.lower(): v for k, v in r.headers.items()}


def decode(raw: bytes, ctype=""):
    m = re.search(r"charset=([\w.-]+)", ctype, re.I)
    encs = ([m.group(1)] if m else []) + ["utf-8", "gb18030"]
    best = None
    for enc in encs:
        try:
            text = raw.decode(enc, "replace")
            score = text.count("\ufffd")
            if best is None or score < best[0]:
                best = (score, text)
        except Exception:
            pass
    return best[1] if best else raw.decode("utf-8", "replace")


def clean_url(raw: str):
    s = html.unescape(raw).replace("\\/", "/").replace("\\u0026", "&")
    if s.startswith("//"):
        s = "http:" + s
    # Trim JavaScript/string punctuation that the permissive regex may retain.
    s = s.rstrip("),;]}")
    return s


def derive_variants(url: str):
    p = urllib.parse.urlsplit(url)
    if not p.hostname or "sinaimg.cn" not in p.hostname.lower():
        return []
    m = PATH_CLASS.search(p.path)
    if not m:
        return [("as_found", url)]
    current = m.group(1).lower()
    out = [("as_found", url)]
    for variant in ("large", "orignal"):
        if current == variant:
            continue
        path = p.path[:m.start()] + f"/{variant}/" + p.path[m.end():]
        out.append((variant, urllib.parse.urlunsplit((p.scheme or "http", p.netloc, path, p.query, p.fragment))))
    return out


def fetch_post(row):
    url = row["post_url"]
    try:
        raw, final, h = get(url, MAX_HTML, "text/html,*/*;q=0.5")
        text = decode(raw, h.get("content-type", ""))
        # Search both raw source and one HTML-unescaped pass.
        blobs = [text, html.unescape(text).replace("\\/", "/")]
        found = set()
        for blob in blobs:
            found.update(clean_url(x) for x in SINA_HTTP.findall(blob))
            found.update(clean_url(x) for x in SINA_URL.findall(blob))
        return row, final, sorted(u for u in found if u.startswith(("http://", "https://"))), ""
    except Exception as exc:
        return row, "", [], f"{type(exc).__name__}: {exc}"


def inspect(item):
    base, variant, url = item
    out = {
        **base,
        "variant": variant,
        "media_url": url,
        "resolved_url": "",
        "http_status": "",
        "content_type": "",
        "bytes": "",
        "sha256": "",
        "width": "",
        "height": "",
        "image_format": "",
        "portrait_ratio": "",
        "likely_document": "",
        "fetch_error": "",
    }
    try:
        raw, final, h = get(url, MAX_IMAGE, "image/*,*/*;q=0.5")
        ctype = h.get("content-type", "").split(";", 1)[0].lower()
        with Image.open(io.BytesIO(raw)) as im:
            w, hg = im.size
            fmt = im.format or ""
        ratio = hg / w if w else 0
        out.update(
            {
                "resolved_url": final,
                "http_status": "200",
                "content_type": ctype,
                "bytes": str(len(raw)),
                "sha256": hashlib.sha256(raw).hexdigest(),
                "width": str(w),
                "height": str(hg),
                "image_format": fmt,
                "portrait_ratio": f"{ratio:.3f}",
                # Newspaper/document triage: portrait-ish and not avatar-size.
                "likely_document": "yes" if w >= 500 and hg >= 650 and ratio >= 1.12 else "no",
            }
        )
    except Exception as exc:
        out["fetch_error"] = f"{type(exc).__name__}: {exc}"
    return out


def main():
    with POSTS.open(newline="", encoding="utf-8") as f:
        all_posts = list(csv.DictReader(f))
    selected = []
    for row in all_posts:
        semantic_text = " ".join(
            [row.get("post_title", ""), row.get("issue_hints", ""), row.get("page_text_hint", "")]
        )
        if row.get("issue_hints") or row.get("page_text_hint") or SEMANTIC.search(semantic_text):
            selected.append(row)
    print(f"selected semantic posts={len(selected)} of {len(all_posts)}", flush=True)

    raw_hits = []
    errors = []
    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        futures = [pool.submit(fetch_post, r) for r in selected]
        for n, fut in enumerate(as_completed(futures), 1):
            row, final, urls, err = fut.result()
            if err:
                errors.append({"post_url": row["post_url"], "error": err})
            if urls:
                print("INLINE", row.get("post_title", ""), "urls", len(urls), flush=True)
            for u in urls:
                raw_hits.append(
                    {
                        "post_url": row["post_url"],
                        "post_date": row.get("post_date", ""),
                        "post_title": row.get("post_title", ""),
                        "issue_hints": row.get("issue_hints", ""),
                        "page_text_hint": row.get("page_text_hint", ""),
                        "resolved_post_url": final,
                        "source_media_url": u,
                    }
                )
            if n % 30 == 0:
                print("post progress", n, "/", len(futures), "raw hits", len(raw_hits), flush=True)

    # De-dup per post/source URL, then derive high-res siblings.
    uniq = {}
    for r in raw_hits:
        uniq[(r["post_url"], r["source_media_url"])] = r
    jobs = []
    for r in uniq.values():
        for variant, u in derive_variants(r["source_media_url"]):
            jobs.append((r, variant, u))

    # Fetch each candidate URL once; retain strongest post association afterwards.
    by_url = {}
    for base, variant, u in jobs:
        key = u
        score = (3 if base.get("issue_hints") else 0) + (2 if base.get("page_text_hint") else 0)
        old = by_url.get(key)
        if old is None or score > old[0]:
            by_url[key] = (score, base, variant, u)
    print(f"unique candidate media={len(by_url)}", flush=True)

    results = []
    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        futures = [pool.submit(inspect, item[1:]) for item in by_url.values()]
        for n, fut in enumerate(as_completed(futures), 1):
            r = fut.result()
            results.append(r)
            if r.get("likely_document") == "yes":
                print("DOCUMENT", r["width"], r["height"], r["post_title"], r["media_url"], flush=True)
            elif n % 40 == 0:
                print("media progress", n, "/", len(futures), flush=True)

    results.sort(
        key=lambda r: (
            r.get("likely_document") != "yes",
            not bool(r.get("issue_hints")),
            r.get("post_date", ""),
            r.get("post_url", ""),
            r.get("media_url", ""),
        )
    )
    fields = [
        "post_url", "post_date", "post_title", "issue_hints", "page_text_hint", "resolved_post_url",
        "source_media_url", "variant", "media_url", "resolved_url", "http_status", "content_type", "bytes",
        "sha256", "width", "height", "image_format", "portrait_ratio", "likely_document", "fetch_error",
    ]
    with OUT.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        w.writeheader()
        w.writerows(results)

    report = {
        "all_sina_posts": len(all_posts),
        "semantic_posts_selected": len(selected),
        "posts_with_inline_media": len({r["post_url"] for r in raw_hits}),
        "raw_inline_media_refs": len(raw_hits),
        "unique_candidate_media_urls": len(by_url),
        "reachable_media": sum(r.get("http_status") == "200" for r in results),
        "likely_document_images": sum(r.get("likely_document") == "yes" for r in results),
        "posts_fetch_errors": len(errors),
        "notes": [
            "Only source URLs already embedded in historical Sina post HTML are considered; large/orignal siblings are deterministic legacy CDN variants.",
            "likely_document is geometry triage only and requires content/OCR verification before archival promotion.",
            "No image bytes are committed.",
        ],
    }
    REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2), flush=True)


if __name__ == "__main__":
    main()
