#!/usr/bin/env python3
"""Recover the 2026 Sohu repost titled 《齐鲁少年》报头版头条点赞振华实验学校.

Starts from the public Sohu education feed already indexed for 2026-06-23, resolves the exact
article link, extracts Sohu CDN images, and records media metadata only. Third-party image bytes
are transient and are not committed.
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
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "data" / "repost_fullpage" / "sohu_2026_zhenhua"
OUT.mkdir(parents=True, exist_ok=True)
FEED = "https://mt.sohu.com/kindfeed/learning/20260623/50"
TITLE = "《齐鲁少年》报头版头条点赞振华实验学校"
UA = "Mozilla/5.0 (compatible; qilu-shaonian-archive/1.0; +https://github.com/Evolution404/qilu-shaonian-electronic-archive)"
MAX_HTML = 8 * 1024 * 1024
MAX_IMAGE = 15 * 1024 * 1024


def get(url: str, limit: int, timeout: int = 25):
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "*/*", "Referer": "https://www.sohu.com/"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        raw = r.read(limit + 1)
        if len(raw) > limit:
            raise ValueError("response too large")
        return raw, r.geturl(), {k.lower(): v for k, v in r.headers.items()}


def decode(raw: bytes):
    for enc in ("utf-8", "gb18030"):
        try:
            return raw.decode(enc)
        except Exception:
            pass
    return raw.decode("utf-8", "replace")


def find_article(feed_html: str):
    # Direct anchors first.
    for m in re.finditer(r'(?is)<a\b[^>]*href=["\']([^"\']+)["\'][^>]*>(.*?)</a>', feed_html):
        text = re.sub(r"(?s)<[^>]+>", " ", m.group(2))
        text = re.sub(r"\s+", "", html.unescape(text))
        if "齐鲁少年" in text and "振华实验学校" in text:
            return urllib.parse.urljoin(FEED, html.unescape(m.group(1)))
    # JSON/escaped URL near exact title.
    compact = html.unescape(feed_html).replace("\\/", "/")
    pos = compact.find(TITLE)
    if pos >= 0:
        chunk = compact[max(0, pos - 3000): pos + 3000]
        urls = re.findall(r'https?://[^"\'<>\s]+', chunk)
        for u in urls:
            if "sohu.com/a/" in u:
                return u.rstrip("\\,}")
    return ""


def image_urls(article_html: str):
    text = html.unescape(article_html).replace("\\/", "/")
    urls = set(re.findall(r'https?://p\d+\.itc\.cn/[^"\'<>\s)]+', text, flags=re.I))
    # Sohu may use src/data-src attributes on other itc.cn hosts.
    urls.update(re.findall(r'https?://[^"\'<>\s)]+\.itc\.cn/[^"\'<>\s)]+', text, flags=re.I))
    clean = []
    for u in urls:
        u = u.rstrip(".,;\\")
        if re.search(r"(?i)\.(?:jpe?g|png|webp)(?:[?&]|$)", u):
            clean.append(u)
    return sorted(set(clean))


def inspect_image(url: str):
    try:
        raw, final, headers = get(url, MAX_IMAGE)
        width = height = fmt = ""
        try:
            im = Image.open(io.BytesIO(raw)); width = str(im.width); height = str(im.height); fmt = im.format or ""
        except Exception:
            pass
        return {
            "source_url": url, "resolved_url": final, "content_type": headers.get("content-type", ""),
            "bytes": str(len(raw)), "sha256": hashlib.sha256(raw).hexdigest(), "width": width, "height": height,
            "image_format": fmt,
            "portrait_page_candidate": "yes" if width and height and int(height) > int(width) * 1.25 and int(height) >= 900 else "no",
            "error": "",
        }
    except Exception as e:
        return {"source_url": url, "resolved_url": "", "content_type": "", "bytes": "", "sha256": "", "width": "", "height": "", "image_format": "", "portrait_page_candidate": "no", "error": f"{type(e).__name__}: {e}"[:1000]}


def main():
    article_url = ""; feed_error = ""; article_error = ""; rows = []
    try:
        raw, _, _ = get(FEED, MAX_HTML)
        article_url = find_article(decode(raw))
    except Exception as e:
        feed_error = f"{type(e).__name__}: {e}"
    if article_url:
        try:
            raw, final, _ = get(article_url, MAX_HTML)
            article_url = final
            for u in image_urls(decode(raw)):
                rows.append(inspect_image(u))
        except Exception as e:
            article_error = f"{type(e).__name__}: {e}"

    fields = ["source_url", "resolved_url", "content_type", "bytes", "sha256", "width", "height", "image_format", "portrait_page_candidate", "error"]
    with (OUT / "images.csv").open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields); w.writeheader(); w.writerows(rows)
    report = {
        "feed_url": FEED,
        "target_title": TITLE,
        "article_url": article_url,
        "feed_error": feed_error,
        "article_error": article_error,
        "image_rows": len(rows),
        "reachable_images": sum(bool(r["sha256"]) for r in rows),
        "portrait_page_candidates": sum(r["portrait_page_candidate"] == "yes" for r in rows),
        "candidate_images": [r for r in rows if r["portrait_page_candidate"] == "yes"],
        "notes": [
            "The exact repost title is required before media is considered relevant.",
            "Portrait geometry is triage only; candidates require content/issue verification before promotion.",
            "No third-party image bytes are committed."
        ]
    }
    (OUT / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2), flush=True)


if __name__ == "__main__":
    main()
