#!/usr/bin/env python3
"""Recover media deleted from current Sina pages by reading historical Wayback snapshots.

Targets the four 2011-12-31 《齐鲁少年》 winter-combined-issue editor-story posts and the
2012-01-10 review/cover post. Current Sina HTML no longer exposes media for the four Dec 31
posts, so this pass asks Wayback CDX for exact historical captures, fetches raw archived HTML,
extracts legacy Sina image/photo URLs, then probes deterministic live CDN variants. Only
metadata/hashes are committed; image bytes remain transient.
"""
from __future__ import annotations

import csv
import hashlib
import html
import io
import json
import re
import time
import urllib.parse
import urllib.request
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
OUTDIR = ROOT / "data" / "repost_fullpage" / "sina_2012_winter_combined" / "wayback"
OUTDIR.mkdir(parents=True, exist_ok=True)
CAPTURES = OUTDIR / "captures.csv"
MEDIA = OUTDIR / "media.csv"
REPORT = OUTDIR / "report.json"
UA = "Mozilla/5.0 qilu-shaonian-winter-wayback/1.0"
PLACEHOLDER_SHA = "d2b5a30568572332968808f1fd3d0218cd8a8ca41889627168fc6d9ca487e766"
MAX_HTML = 7 * 1024 * 1024
MAX_IMG = 25 * 1024 * 1024

POSTS = [
    ("2011-12-31", "2012年寒假合刊编辑部的故事之三", "https://blog.sina.com.cn/s/blog_4c4fc7d9010116nf.html"),
    ("2011-12-31", "2012年寒假合刊编辑部的故事之四和六", "https://blog.sina.com.cn/s/blog_4c4fc7d9010116ni.html"),
    ("2011-12-31", "2012年寒假合刊编辑部的故事之五", "https://blog.sina.com.cn/s/blog_4c4fc7d9010116nl.html"),
    ("2011-12-31", "2012年寒假合刊编辑部的故事之七", "https://blog.sina.com.cn/s/blog_4c4fc7d9010116nn.html"),
    ("2012-01-10", "今天我评报", "https://blog.sina.com.cn/s/blog_4c4fc7d901011cp9.html"),
]

ABS_URL_RE = re.compile(r'''(?i)https?://[^"'<>\s\\]+''')
PROTO_URL_RE = re.compile(r'''(?i)(?://|https?:\\?/\\?/)[^"'<>\s]+''')
MEDIA_RE = re.compile(
    r'''(?i)(?:s\d+|ss\d+|photo|album|ww\d+|wx\d+)\.(?:sinaimg\.cn|sina\.com\.cn)/[^"'<>\s\\]+'''
)
LEGACY_KEY_RE = re.compile(r'''(?i)/(?:middle|bmiddle|large|orignal|thumbnail|mw\d+|orj\d+|square)/([^?"'<>\s]+)''')
PATH_CLASS = re.compile(r"/(middle|bmiddle|large|orignal|thumbnail|mw\d+|orj\d+|square)/", re.I)
HOST_RE = re.compile(r"^(ss?|SS?)(\d+)\.sinaimg\.cn$", re.I)


def request(url: str, limit: int, referer: str = "", accept: str = "*/*", timeout: int = 25):
    h = {"User-Agent": UA, "Accept": accept}
    if referer:
        h["Referer"] = referer
    req = urllib.request.Request(url, headers=h)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        body = r.read(limit + 1)
        if len(body) > limit:
            raise ValueError("response too large")
        return body, r.geturl(), {k.lower(): v for k, v in r.headers.items()}


def decode(body: bytes, ctype: str = ""):
    candidates = []
    m = re.search(r"charset=([\w.-]+)", ctype, re.I)
    if m:
        candidates.append(m.group(1))
    candidates += ["utf-8", "gb18030"]
    best = None
    for enc in candidates:
        try:
            text = body.decode(enc, "replace")
            score = text.count("\ufffd")
            if best is None or score < best[0]:
                best = (score, text)
        except Exception:
            pass
    return best[1] if best else body.decode("utf-8", "replace")


def cdx(url: str):
    qs = urllib.parse.urlencode({
        "url": url,
        "output": "json",
        "fl": "timestamp,original,statuscode,mimetype,digest",
        "filter": "statuscode:200",
        "from": "2011",
        "to": "2013",
        "collapse": "digest",
    })
    endpoint = "https://web.archive.org/cdx/search/cdx?" + qs
    body, _, _ = request(endpoint, 3 * 1024 * 1024, accept="application/json,*/*", timeout=30)
    data = json.loads(body.decode("utf-8", "replace"))
    if not data or len(data) < 2:
        return []
    hdr = data[0]
    return [dict(zip(hdr, row)) for row in data[1:] if len(row) == len(hdr)]


def normalize_media(raw: str):
    s = html.unescape(raw).replace("\\/", "/").replace("\\u0026", "&")
    if s.startswith("//"):
        s = "http:" + s
    s = s.strip('"\'()[]{};,')
    return s


def extract_media(text: str):
    blobs = [text, html.unescape(text), html.unescape(text).replace("\\/", "/")]
    urls = set()
    for blob in blobs:
        for m in ABS_URL_RE.findall(blob):
            if "sinaimg" in m.lower() or re.search(r"(?i)(photo|album)\.sina\.com\.cn", m):
                urls.add(normalize_media(m))
        for m in PROTO_URL_RE.findall(blob):
            x = normalize_media(m)
            if "sinaimg" in x.lower() or re.search(r"(?i)(photo|album)\.sina\.com\.cn", x):
                urls.add(x)
        for m in MEDIA_RE.findall(blob):
            x = normalize_media(m)
            if not x.startswith(("http://", "https://")):
                x = "http://" + x
            urls.add(x)
    return sorted(u for u in urls if len(u) < 1200)


def variants(url: str):
    p = urllib.parse.urlsplit(url)
    host = (p.hostname or "").lower()
    m = PATH_CLASS.search(p.path)
    if "sinaimg.cn" not in host or not m:
        return [("as_found", url)]
    tail = p.path[m.end():]
    if len(tail) < 8:
        return []
    hosts = [p.netloc]
    hm = HOST_RE.match(host)
    if hm:
        n = hm.group(2)
        alt = ("s" if host.startswith("ss") else "ss") + n + ".sinaimg.cn"
        hosts.append(alt)
    out = []
    for netloc in dict.fromkeys(hosts):
        for scheme in ("https", "http"):
            for cls in dict.fromkeys([m.group(1).lower(), "middle", "bmiddle", "large", "orignal"]):
                path = p.path[:m.start()] + f"/{cls}/" + p.path[m.end():]
                out.append((f"{netloc}:{cls}:{scheme}", urllib.parse.urlunsplit((scheme, netloc, path, p.query, p.fragment))))
    return list(dict.fromkeys(out))


def inspect_media(base, variant_name, url):
    out = {**base, "variant": variant_name, "candidate_url": url, "resolved_url": "", "http_status": "", "content_type": "", "bytes": "", "sha256": "", "width": "", "height": "", "image_format": "", "is_placeholder": "", "likely_document": "", "error": ""}
    try:
        body, final, h = request(url, MAX_IMG, referer=base["post_url"], accept="image/*,*/*;q=0.6", timeout=20)
        with Image.open(io.BytesIO(body)) as im:
            w, hg = im.size
            fmt = im.format or ""
        sha = hashlib.sha256(body).hexdigest()
        placeholder = sha == PLACEHOLDER_SHA or "default_s_" in final or (w == 360 and hg == 360 and fmt.upper() == "GIF")
        ratio = hg / w if w else 0
        out.update({"resolved_url": final, "http_status": "200", "content_type": h.get("content-type", "").split(";", 1)[0], "bytes": str(len(body)), "sha256": sha, "width": str(w), "height": str(hg), "image_format": fmt, "is_placeholder": "yes" if placeholder else "no", "likely_document": "yes" if (not placeholder and w >= 500 and hg >= 650 and ratio >= 1.12) else "no"})
    except Exception as exc:
        out["error"] = f"{type(exc).__name__}: {exc}"[:1000]
    return out


def main():
    capture_rows = []
    media_sources = []
    errors = []
    for post_date, title, post_url in POSTS:
        try:
            records = cdx(post_url)
        except Exception as exc:
            errors.append({"post_url": post_url, "stage": "cdx", "error": f"{type(exc).__name__}: {exc}"})
            records = []
        print(title, "captures", len(records), flush=True)
        for rec in records[:8]:
            ts = rec.get("timestamp", "")
            orig = rec.get("original") or post_url
            replay = f"https://web.archive.org/web/{ts}id_/{orig}"
            crow = {"post_date": post_date, "post_title": title, "post_url": post_url, "timestamp": ts, "original": orig, "mimetype": rec.get("mimetype", ""), "digest": rec.get("digest", ""), "replay_url": replay, "archive_recovered": "no", "bytes": "", "media_refs": "0", "error": ""}
            try:
                body, final, h = request(replay, MAX_HTML, accept="text/html,*/*;q=0.5", timeout=30)
                text = decode(body, h.get("content-type", ""))
                # Reject current IA landing/error pages rather than treating them as historical HTML.
                low = text[:12000].lower()
                if "internet archive" in low and "wayback machine" in low and len(body) < 400000:
                    raise ValueError("wayback landing page instead of archived post")
                found = extract_media(text)
                crow.update({"replay_url": final, "archive_recovered": "yes", "bytes": str(len(body)), "media_refs": str(len(found))})
                for u in found:
                    keym = LEGACY_KEY_RE.search(urllib.parse.urlsplit(u).path)
                    media_sources.append({"post_date": post_date, "post_title": title, "post_url": post_url, "capture_timestamp": ts, "archived_post_url": final, "source_media_url": u, "legacy_key": keym.group(1) if keym else ""})
            except Exception as exc:
                crow["error"] = f"{type(exc).__name__}: {exc}"[:1000]
            capture_rows.append(crow)
            time.sleep(0.15)

    # Deduplicate media by source URL, preferring Dec 31 posts over Jan 10 cover post.
    by = {}
    for r in media_sources:
        old = by.get(r["source_media_url"])
        score = 2 if r["post_date"] == "2011-12-31" else 1
        if old is None or score > old[0]:
            by[r["source_media_url"]] = (score, r)

    results = []
    for _, src in by.values():
        for vname, u in variants(src["source_media_url"]):
            results.append(inspect_media(src, vname, u))
            if len(results) % 20 == 0:
                print("media probes", len(results), flush=True)

    capture_fields = ["post_date", "post_title", "post_url", "timestamp", "original", "mimetype", "digest", "replay_url", "archive_recovered", "bytes", "media_refs", "error"]
    with CAPTURES.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=capture_fields); w.writeheader(); w.writerows(capture_rows)
    media_fields = ["post_date", "post_title", "post_url", "capture_timestamp", "archived_post_url", "source_media_url", "legacy_key", "variant", "candidate_url", "resolved_url", "http_status", "content_type", "bytes", "sha256", "width", "height", "image_format", "is_placeholder", "likely_document", "error"]
    with MEDIA.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=media_fields); w.writeheader(); w.writerows(results)

    dec31 = [r for r in results if r["post_date"] == "2011-12-31"]
    report = {
        "posts_targeted": len(POSTS),
        "cdx_captures": len(capture_rows),
        "archived_posts_recovered": sum(r["archive_recovered"] == "yes" for r in capture_rows),
        "historical_media_refs": len(media_sources),
        "unique_historical_media_urls": len(by),
        "media_variants_probed": len(results),
        "reachable_non_placeholder": sum(r["http_status"] == "200" and r["is_placeholder"] == "no" for r in results),
        "dec31_source_media_urls": len({r["source_media_url"] for r in dec31}),
        "dec31_reachable_non_placeholder": sum(r["http_status"] == "200" and r["is_placeholder"] == "no" for r in dec31),
        "dec31_likely_document": sum(r["likely_document"] == "yes" for r in dec31),
        "errors": errors,
        "notes": [
            "Historical Wayback HTML is used specifically to recover media references deleted from current Sina HTML.",
            "No image bytes or archived HTML bodies are committed; only source/capture URLs, hashes and dimensions are stored.",
            "Any recovered image still requires content/visual verification before promotion as a newspaper page."
        ]
    }
    REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2), flush=True)

if __name__ == "__main__":
    main()
