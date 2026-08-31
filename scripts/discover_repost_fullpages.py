#!/usr/bin/env python3
"""Scan historical editor/reader archives for original newspaper page images.

Sources:
- Sina blog of 《齐鲁少年》 editorial staff (user 1280296921 / blog id 4c4fc7d9)
- qlsnreadship.wordpress.com reader archive

Sina's legacy article-list endpoint now often returns HTTP 418. Instead of treating that as
"no posts", this crawler starts from verified 2009/2011 posts and walks the blog's own
previous/next-post graph. It also understands Sina's legacy ``real_src`` lazy-image field and
probes ``large`` variants of resized sinaimg.cn URLs.

The script stores URLs and technical metadata only. Image bytes are fetched transiently
for dimension/hash checks and are never committed.
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
from collections import deque
from concurrent.futures import ThreadPoolExecutor, as_completed
from html.parser import HTMLParser
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
OUTDIR = ROOT / "data" / "repost_fullpage"
OUTDIR.mkdir(parents=True, exist_ok=True)
UA = "qilu-shaonian-repost-fullpage/2.0 (+https://github.com/Evolution404/qilu-shaonian-electronic-archive)"
TIMEOUT = 20
MAX_BYTES = 24 * 1024 * 1024
WORKERS = 16
MAX_SINA_POSTS = 650

SINA_SEEDS = [
    "https://blog.sina.com.cn/s/blog_4c4fc7d90100el5d.html",  # 2009 verified editor post
    "https://blog.sina.com.cn/s/blog_4c4fc7d90100zv7h.html",  # 2011 issue-1000 post
]
WP_API = "https://public-api.wordpress.com/rest/v1.1/sites/qlsnreadship.wordpress.com/posts/"

BLOG_RE = re.compile(r"(?:https?://blog\.sina\.com\.cn/s/)?(blog_4c4fc7d9[0-9a-f]+\.html)", re.I)
IMG_EXT = re.compile(r"\.(?:jpe?g|png|gif|webp)(?:$|\?)", re.I)
ISSUE_TEXT = re.compile(r"(?:第\s*([0-9０-９]{2,5})\s*期|([0-9０-９]{2,5})\s*期)")
PAGE_TEXT = re.compile(r"(?:第\s*[A-DＡ-Ｄ0-9０-９]{1,3}\s*版|[A-DＡ-Ｄ][0-9０-９]?\s*版|头版|整版)")
DATE_RE = re.compile(r"\((20\d{2}-\d{2}-\d{2})\s+\d{2}:\d{2}:\d{2}\)")
TITLE_RE = re.compile(r"(?is)<title>(.*?)</title>")


class Parser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.links = []
        self.images = []
        self.text = []

    def handle_starttag(self, tag, attrs):
        d = {k.lower(): (v or "") for k, v in attrs}
        tag = tag.lower()
        if tag == "a" and d.get("href"):
            self.links.append(d["href"])
        if tag in {"img", "source"}:
            for key in (
                "src",
                "real_src",
                "data-src",
                "data-original",
                "data-lazy-src",
                "data-actualsrc",
            ):
                if d.get(key):
                    self.images.append(d[key])
            if d.get("srcset"):
                for part in d["srcset"].split(","):
                    if part.strip():
                        self.images.append(part.strip().split()[0])
        if tag == "meta":
            k = (d.get("property") or d.get("name") or "").lower()
            if k in {"og:image", "twitter:image", "twitter:image:src"} and d.get("content"):
                self.images.append(d["content"])

    def handle_data(self, data):
        s = " ".join(data.split())
        if s:
            self.text.append(s)


def req(url, max_bytes=None):
    r = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "*/*"})
    with urllib.request.urlopen(r, timeout=TIMEOUT) as x:
        data = x.read() if max_bytes is None else x.read(max_bytes + 1)
        if max_bytes is not None and len(data) > max_bytes:
            raise ValueError("too large")
        return data, x.geturl(), {k.lower(): v for k, v in x.headers.items()}


def decode(raw, ctype=""):
    m = re.search(r"charset=([\w.-]+)", ctype, re.I)
    encs = [m.group(1)] if m else []
    encs += ["utf-8", "gb18030"]
    best = None
    for enc in encs:
        try:
            t = raw.decode(enc, "replace")
            score = t.count("\ufffd")
            if best is None or score < best[0]:
                best = (score, t)
        except Exception:
            pass
    return best[1] if best else raw.decode("utf-8", "replace")


def parse_html(url):
    raw, final, h = req(url)
    source = decode(raw, h.get("content-type", ""))
    p = Parser()
    p.feed(source)
    visible = html.unescape(" ".join(p.text))
    return final, p, visible, source


def normalize_blog_link(raw):
    m = BLOG_RE.search(html.unescape(raw or ""))
    if not m:
        return ""
    return "https://blog.sina.com.cn/s/" + m.group(1)


def normalize_media(base, raw):
    v = html.unescape(raw.strip())
    if v.startswith("//"):
        v = "https:" + v
    u = urllib.parse.urljoin(base, v)
    if not u.startswith(("http://", "https://")):
        return ""
    return u


def sina_large_variant(url):
    p = urllib.parse.urlsplit(url)
    if not p.hostname or "sinaimg.cn" not in p.hostname.lower():
        return ""
    path = p.path
    new = re.sub(r"/(?:thumbnail|bmiddle|mw\d+|orj\d+|square)/", "/large/", path, count=1, flags=re.I)
    if new == path:
        return ""
    return urllib.parse.urlunsplit((p.scheme or "https", p.netloc, new, p.query, p.fragment))


def post_metadata(url, final, parser, visible, source):
    issues = []
    for a, b in ISSUE_TEXT.findall(visible):
        issues.append(a or b)
    issues = list(dict.fromkeys(issues))[:30]
    page_hint = "yes" if PAGE_TEXT.search(visible) else ""
    dm = DATE_RE.search(visible)
    tm = TITLE_RE.search(source)
    title = ""
    if tm:
        title = re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", tm.group(1)))).strip()
    return {
        "source": "sina_editor_blog",
        "post_url": url,
        "resolved_post_url": final,
        "post_date": dm.group(1) if dm else "",
        "post_title": title,
        "issue_hints": "|".join(issues),
        "page_text_hint": page_hint,
        "images": parser.images,
    }


def discover_sina_chain():
    """Walk the editor blog graph from known posts; returns posts + media rows."""
    pending = deque(SINA_SEEDS)
    queued = set(SINA_SEEDS)
    seen = set()
    post_rows = []
    media_rows = []
    errors = []

    while pending and len(seen) < MAX_SINA_POSTS:
        batch = []
        while pending and len(batch) < 12 and len(seen) + len(batch) < MAX_SINA_POSTS:
            u = pending.popleft()
            if u not in seen:
                batch.append(u)
        if not batch:
            break

        def one(url):
            try:
                final, p, visible, source = parse_html(url)
                meta = post_metadata(url, final, p, visible, source)
                neighbors = set()
                for href in p.links:
                    n = normalize_blog_link(href)
                    if n:
                        neighbors.add(n)
                for match in BLOG_RE.finditer(source):
                    neighbors.add("https://blog.sina.com.cn/s/" + match.group(1))
                return meta, neighbors, None
            except Exception as exc:
                return None, set(), f"{type(exc).__name__}: {exc}"

        with ThreadPoolExecutor(max_workers=min(12, len(batch))) as pool:
            futures = {pool.submit(one, u): u for u in batch}
            for fut in as_completed(futures):
                url = futures[fut]
                seen.add(url)
                meta, neighbors, err = fut.result()
                if err:
                    errors.append({"source": "sina_chain", "url": url, "error": err})
                    continue
                post_rows.append({k: v for k, v in meta.items() if k != "images"})
                for raw in meta["images"]:
                    media = normalize_media(meta["resolved_post_url"], raw)
                    if not media:
                        continue
                    base = {
                        "source": "sina_editor_blog",
                        "post_url": meta["post_url"],
                        "resolved_post_url": meta["resolved_post_url"],
                        "post_date": meta["post_date"],
                        "post_title": meta["post_title"],
                        "issue_hints": meta["issue_hints"],
                        "page_text_hint": meta["page_text_hint"],
                    }
                    media_rows.append({**base, "media_url": media, "media_variant": "html_reference"})
                    large = sina_large_variant(media)
                    if large and large != media:
                        media_rows.append({**base, "media_url": large, "media_variant": "sina_large_variant"})
                for n in neighbors:
                    if n not in seen and n not in queued and len(queued) < MAX_SINA_POSTS + 50:
                        queued.add(n)
                        pending.append(n)
        print("sina chain posts", len(seen), "pending", len(pending), flush=True)

    return post_rows, media_rows, errors


def discover_wp_posts():
    posts = {}
    errors = []
    url = WP_API + "?number=100"
    for _ in range(20):
        try:
            raw, _, _ = req(url)
            data = json.loads(raw.decode("utf-8", "replace"))
            for post in data.get("posts", []):
                u = post.get("URL") or post.get("url")
                if u:
                    posts[u] = "wordpress_reader_archive"
            meta = data.get("meta", {}) or {}
            handle = meta.get("next_page") or meta.get("next_page_handle")
            if not handle:
                break
            url = WP_API + "?number=100&page_handle=" + urllib.parse.quote(str(handle))
        except Exception as e:
            errors.append({"source": "wordpress_api", "url": url, "error": f"{type(e).__name__}: {e}"})
            break
    posts[
        "https://qlsnreadship.wordpress.com/%E5%B0%8F%E8%AF%BB%E8%80%85%E7%BE%A4-%E5%8D%81%E5%91%A8%E5%B9%B4/"
    ] = "wordpress_reader_archive"
    return posts, errors


def scan_wp_post(item):
    url, source_name = item
    rows = []
    try:
        final, p, visible, source = parse_html(url)
        issues = []
        for a, b in ISSUE_TEXT.findall(visible):
            issues.append(a or b)
        issues = list(dict.fromkeys(issues))[:30]
        page_hint = "yes" if PAGE_TEXT.search(visible) else ""
        dm = DATE_RE.search(visible)
        tm = TITLE_RE.search(source)
        title = ""
        if tm:
            title = re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", tm.group(1)))).strip()
        seen = set()
        for raw in p.images:
            media = normalize_media(final, raw)
            if not media or media in seen:
                continue
            seen.add(media)
            rows.append(
                {
                    "source": source_name,
                    "post_url": url,
                    "resolved_post_url": final,
                    "post_date": dm.group(1) if dm else "",
                    "post_title": title,
                    "issue_hints": "|".join(issues),
                    "page_text_hint": page_hint,
                    "media_url": media,
                    "media_variant": "html_reference",
                }
            )
        return rows, None
    except Exception as e:
        return [], {"source": source_name, "url": url, "error": f"{type(e).__name__}: {e}"}


def inspect_url(url):
    out = {
        "resolved_media_url": "",
        "http_status": "",
        "content_type": "",
        "content_length": "",
        "sha256": "",
        "width": "",
        "height": "",
        "image_format": "",
        "portrait_ratio": "",
        "likely_page_scan": "",
        "fetch_error": "",
    }
    try:
        raw, final, h = req(url, MAX_BYTES)
        c = h.get("content-type", "").split(";", 1)[0]
        out.update(
            {
                "resolved_media_url": final,
                "http_status": "200",
                "content_type": c,
                "content_length": str(len(raw)),
                "sha256": hashlib.sha256(raw).hexdigest(),
            }
        )
        if c.startswith("image/") or IMG_EXT.search(final):
            with Image.open(io.BytesIO(raw)) as im:
                w, hg = im.size
                ratio = hg / w if w else 0
                out.update(
                    {
                        "width": str(w),
                        "height": str(hg),
                        "image_format": str(im.format or ""),
                        "portrait_ratio": f"{ratio:.3f}",
                    }
                )
                # Old Sina originals are often only 600-700px wide; keep a looser triage
                # threshold and let Chinese OCR/content checks reject portraits/photos later.
                out["likely_page_scan"] = "yes" if w >= 550 and hg >= 780 and ratio >= 1.10 else "no"
    except Exception as e:
        out["fetch_error"] = f"{type(e).__name__}: {e}"
    return out


def main():
    sina_posts, sina_media, e1 = discover_sina_chain()
    wp, e2 = discover_wp_posts()
    errors = e1 + e2
    media = list(sina_media)

    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        futures = {pool.submit(scan_wp_post, item): item for item in wp.items()}
        for fut in as_completed(futures):
            rows, err = fut.result()
            media.extend(rows)
            if err:
                errors.append(err)

    # Keep post associations but fetch/hash each unique media URL only once.
    uniq_refs = {}
    for r in media:
        key = (r["source"], r["post_url"], r["media_url"], r.get("media_variant", ""))
        uniq_refs[key] = r
    media = list(uniq_refs.values())
    unique_urls = sorted({r["media_url"] for r in media})
    print(
        f"posts discovered={len(sina_posts)+len(wp)} sina={len(sina_posts)} wp={len(wp)} "
        f"media refs={len(media)} unique media={len(unique_urls)}",
        flush=True,
    )

    meta_by_url = {}
    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        futures = {pool.submit(inspect_url, u): u for u in unique_urls}
        for n, fut in enumerate(as_completed(futures), 1):
            u = futures[fut]
            meta_by_url[u] = fut.result()
            m = meta_by_url[u]
            if m.get("likely_page_scan") == "yes":
                print("PAGE-CANDIDATE", m["width"], m["height"], u, flush=True)
            elif n % 100 == 0:
                print("media progress", n, "/", len(futures), flush=True)

    inspected = []
    for r in media:
        inspected.append({**r, **meta_by_url.get(r["media_url"], {})})
    inspected.sort(
        key=lambda r: (
            r.get("likely_page_scan") != "yes",
            r["source"],
            r.get("post_date", ""),
            r["post_url"],
            r["media_url"],
        )
    )

    post_fields = [
        "source",
        "post_url",
        "resolved_post_url",
        "post_date",
        "post_title",
        "issue_hints",
        "page_text_hint",
    ]
    with (OUTDIR / "sina_posts.csv").open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=post_fields, extrasaction="ignore")
        w.writeheader()
        w.writerows(sorted(sina_posts, key=lambda r: (r.get("post_date", ""), r["post_url"])))

    fields = post_fields + [
        "media_url",
        "media_variant",
        "resolved_media_url",
        "http_status",
        "content_type",
        "content_length",
        "sha256",
        "width",
        "height",
        "image_format",
        "portrait_ratio",
        "likely_page_scan",
        "fetch_error",
    ]
    with (OUTDIR / "media_candidates.csv").open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        w.writeheader()
        w.writerows(inspected)

    report = {
        "sina_posts": len(sina_posts),
        "wordpress_posts": len(wp),
        "total_posts": len(sina_posts) + len(wp),
        "media_refs": len(media),
        "unique_media_urls": len(unique_urls),
        "reachable_media": sum(1 for u in unique_urls if meta_by_url[u].get("http_status") == "200"),
        "likely_page_scan_refs": sum(1 for r in inspected if r.get("likely_page_scan") == "yes"),
        "likely_page_scan_unique_urls": len(
            {r["media_url"] for r in inspected if r.get("likely_page_scan") == "yes"}
        ),
        "errors": errors[:300],
        "notes": [
            "Sina discovery walks previous/next post links because legacy article-list pages may return HTTP 418.",
            "Sina real_src and large image variants are probed to avoid missing historical original-resolution uploads.",
            "Candidates require OCR/visual verification before promotion.",
            "Only metadata is committed; third-party image bytes are transient.",
        ],
    }
    (OUTDIR / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
