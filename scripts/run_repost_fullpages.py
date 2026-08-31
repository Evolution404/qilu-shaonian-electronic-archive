#!/usr/bin/env python3
"""Run the repost scanner with corrected Sina id grammar + monthly archive seeds.

Two historical Sina quirks matter here:
1. blog ids are alphanumeric/base36-like, not hexadecimal;
2. previous/next links can be blocked by HTTP 418, while ``art365list`` monthly archive
   pages are still independently indexed/reachable.

This wrapper discovers article ids from monthly archive pages first, then hands the expanded
seed set to the existing post/image/OCR crawler.
"""
from __future__ import annotations

import html
import re
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed

import discover_repost_fullpages as scanner

scanner.BLOG_RE = re.compile(
    r"(?:https?://blog\.sina\.com\.cn/s/)?(blog_4c4fc7d9[0-9a-z]+\.html)",
    re.I,
)

ARCHIVE_URLS = [
    f"https://blog.sina.com.cn/s/art365list_1280296921_{year}_{month:02d}.html"
    for year in range(2007, 2025)
    for month in range(1, 13)
]


def fetch_archive(url: str):
    try:
        req = urllib.request.Request(
            url,
            headers={
                "User-Agent": "Mozilla/5.0 qilu-shaonian-sina-monthly-archive/1.0",
                "Accept": "text/html,*/*;q=0.8",
            },
        )
        with urllib.request.urlopen(req, timeout=10) as r:
            raw = r.read(4 * 1024 * 1024)
        text = raw.decode("utf-8", "replace")
        # Decode entities because some legacy pages/script blobs escape link markup.
        text = html.unescape(text)
        posts = {
            "https://blog.sina.com.cn/s/" + m.group(1)
            for m in scanner.BLOG_RE.finditer(text)
        }
        return posts, ""
    except Exception as exc:
        return set(), f"{type(exc).__name__}: {exc}"


def discover_archive_seeds():
    posts = set(scanner.SINA_SEEDS)
    hits = 0
    errors = 0
    with ThreadPoolExecutor(max_workers=16) as pool:
        futures = {pool.submit(fetch_archive, u): u for u in ARCHIVE_URLS}
        for n, fut in enumerate(as_completed(futures), 1):
            found, err = fut.result()
            if found:
                hits += 1
                posts.update(found)
            if err:
                errors += 1
            if n % 36 == 0:
                print(
                    "monthly archives",
                    n,
                    "/",
                    len(futures),
                    "pages_with_posts",
                    hits,
                    "unique_posts",
                    len(posts),
                    "errors",
                    errors,
                    flush=True,
                )
    print(
        "monthly archive seed result",
        "pages_with_posts",
        hits,
        "unique_posts",
        len(posts),
        "errors",
        errors,
        flush=True,
    )
    return sorted(posts)


scanner.SINA_SEEDS = discover_archive_seeds()
# Allow the chain walker to process the full known blog rather than stopping at its old 650
# guard if monthly discovery approaches the documented 537-post total.
scanner.MAX_SINA_POSTS = max(scanner.MAX_SINA_POSTS, 700)

if __name__ == "__main__":
    raise SystemExit(scanner.main())
