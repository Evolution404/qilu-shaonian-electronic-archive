#!/usr/bin/env python3
"""Recover Sina blog media URLs hidden in raw/escaped source for issue-specific posts.

Legacy Sina blog HTML can hide images in ``real_src`` attributes, escaped fragments or
script strings.  This pass extracts only URLs present in the verified editor-blog HTML,
then probes deterministic historical CDN siblings.  Old Sina used both sN.sinaimg.cn and
ssN.sinaimg.cn hosts and paths such as middle/bmiddle/large/orignal.  Image requests carry
the source blog post as Referer.  Bytes are transient; only metadata and hashes persist.
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
UA = "Mozilla/5.0 qilu-shaonian-sina-inline-media/2.0"
TIMEOUT = 14
MAX_HTML = 5 * 1024 * 1024
MAX_IMAGE = 25 * 1024 * 1024
WORKERS = 12
PLACEHOLDER_SHA256 = "d2b5a30568572332968808f1fd3d0218cd8a8ca41889627168fc6d9ca487e766"

SEMANTIC = re.compile(r"评报|看图说事|版面|合刊|第\s*\d{3,5}\s*期|\d{3,5}\s*期|一版|二版|三版|四版|头版|报纸", re.I)
SINA_URL = re.compile(
    r'''(?i)(?:https?:)?(?:\\?/\\?/|//)(?:s\d+|ss\d+|photo|album|ww\d+|wx\d+)\.sinaimg\.cn(?:\\?/|/)[^"'<>\s\\]+'''
)
SINA_HTTP = re.compile(r'''(?i)https?://(?:s\d+|ss\d+|photo|album|ww\d+|wx\d+)\.sinaimg\.cn/[^"'<>\s]+''')
PATH_CLASS = re.compile(r"/(middle|bmiddle|thumbnail|mw\d+|orj\d+|square|large|orignal)/", re.I)
HOST_S = re.compile(r"^s(\d+)\.sinaimg\.cn$", re.I)
HOST_SS = re.compile(r"^ss(\d+)\.sinaimg\.cn$", re.I)


def get(url: str, limit: int, accept="*/*", referer=""):
    headers = {"User-Agent": UA, "Accept": accept}
    if referer:
        headers["Referer"] = referer
    req = urllib.request.Request(url, headers=headers)
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
    return s.rstrip("),;]}")


def derive_variants(url: str):
    p = urllib.parse.urlsplit(url)
    host = (p.hostname or "").lower()
    if "sinaimg.cn" not in host:
        return []
    m = PATH_CLASS.search(p.path)
    if not m:
        return []
    tail = p.path[m.end():]
    if len(tail) < 8:
        return []

    hosts = [p.netloc]
    sm = HOST_S.match(host)
    ssm = HOST_SS.match(host)
    if sm:
        hosts.append(re.sub(r"^s\d+", f"ss{sm.group(1)}", p.netloc, flags=re.I))
    elif ssm:
        hosts.append(re.sub(r"^ss\d+", f"s{ssm.group(1)}", p.netloc, flags=re.I))

    path_classes = [m.group(1).lower(), "middle", "bmiddle", "large", "orignal"]
    out = []
    for netloc in dict.fromkeys(hosts):
        for scheme in (p.scheme or "http", "http", "https"):
            for cls in dict.fromkeys(path_classes):
                path = p.path[:m.start()] + f"/{cls}/" + p.path[m.end():]
                candidate = urllib.parse.urlunsplit((scheme, netloc, path, p.query, p.fragment))
                out.append((f"{netloc}:{cls}:{scheme}", candidate))
    return list(dict.fromkeys(out))


def fetch_post(row):
    url = row["post_url"]
    try:
        raw, final, h = get(url, MAX_HTML, "text/html,*/*;q=0.5")
        text = decode(raw, h.get("content-type", ""))
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
        "is_placeholder": "",
        "fetch_error": "",
    }
    try:
        raw, final, h = get(url, MAX_IMAGE, "image/*,*/*;q=0.5", base.get("post_url", ""))
        ctype = h.get("content-type", "").split(";", 1)[0].lower()
        with Image.open(io.BytesIO(raw)) as im:
            w, hg = im.size
            fmt = im.format or ""
        digest = hashlib.sha256(raw).hexdigest()
        ratio = hg / w if w else 0
        placeholder = digest == PLACEHOLDER_SHA256 or "default_s_" in final or (w == 360 and hg == 360 and fmt.upper() == "GIF")
        out.update({
            "resolved_url": final,
            "http_status": "200",
            "content_type": ctype,
            "bytes": str(len(raw)),
            "sha256": digest,
            "width": str(w),
            "height": str(hg),
            "image_format": fmt,
            "portrait_ratio": f"{ratio:.3f}",
            "likely_document": "yes" if not placeholder and w >= 500 and hg >= 650 and ratio >= 1.12 else "no",
            "is_placeholder": "yes" if placeholder else "no",
        })
    except Exception as exc:
        out["fetch_error"] = f"{type(exc).__name__}: {exc}"
    return out


def main():
    with POSTS.open(newline="", encoding="utf-8") as f:
        all_posts = list(csv.DictReader(f))
    selected = []
    for row in all_posts:
        semantic_text = " ".join([row.get("post_title", ""), row.get("issue_hints", ""), row.get("page_text_hint", "")])
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
            for u in urls:
                raw_hits.append({
                    "post_url": row["post_url"], "post_date": row.get("post_date", ""),
                    "post_title": row.get("post_title", ""), "issue_hints": row.get("issue_hints", ""),
                    "page_text_hint": row.get("page_text_hint", ""), "resolved_post_url": final,
                    "source_media_url": u,
                })
            if n % 30 == 0:
                print("post progress", n, "/", len(futures), "raw hits", len(raw_hits), flush=True)

    uniq = {}
    for r in raw_hits:
        uniq[(r["post_url"], r["source_media_url"])] = r
    jobs = []
    for r in uniq.values():
        for variant, u in derive_variants(r["source_media_url"]):
            jobs.append((r, variant, u))

    by_url = {}
    for base, variant, u in jobs:
        score = (3 if base.get("issue_hints") else 0) + (2 if base.get("page_text_hint") else 0)
        old = by_url.get(u)
        if old is None or score > old[0]:
            by_url[u] = (score, base, variant, u)
    print(f"unique candidate media={len(by_url)}", flush=True)

    results = []
    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        futures = [pool.submit(inspect, item[1:]) for item in by_url.values()]
        for n, fut in enumerate(as_completed(futures), 1):
            r = fut.result(); results.append(r)
            if r.get("http_status") == "200" and r.get("is_placeholder") == "no":
                print("REAL", r["width"], r["height"], r["post_title"], r["media_url"], flush=True)
            elif n % 50 == 0:
                print("media progress", n, "/", len(futures), flush=True)

    results.sort(key=lambda r: (
        r.get("is_placeholder") != "no", r.get("likely_document") != "yes",
        not bool(r.get("issue_hints")), r.get("post_date", ""), r.get("post_url", ""), r.get("media_url", "")
    ))
    fields = [
        "post_url", "post_date", "post_title", "issue_hints", "page_text_hint", "resolved_post_url",
        "source_media_url", "variant", "media_url", "resolved_url", "http_status", "content_type", "bytes",
        "sha256", "width", "height", "image_format", "portrait_ratio", "likely_document", "is_placeholder", "fetch_error",
    ]
    with OUT.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore"); w.writeheader(); w.writerows(results)

    report = {
        "all_sina_posts": len(all_posts),
        "semantic_posts_selected": len(selected),
        "posts_with_inline_media": len({r["post_url"] for r in raw_hits}),
        "raw_inline_media_refs": len(raw_hits),
        "unique_candidate_media_urls": len(by_url),
        "reachable_media": sum(r.get("http_status") == "200" for r in results),
        "reachable_non_placeholder": sum(r.get("http_status") == "200" and r.get("is_placeholder") == "no" for r in results),
        "likely_document_images": sum(r.get("likely_document") == "yes" for r in results),
        "issue_hint_non_placeholder": sum(bool(r.get("issue_hints")) and r.get("is_placeholder") == "no" for r in results),
        "posts_fetch_errors": len(errors),
        "notes": [
            "Only media keys embedded in historical editor-blog HTML are probed.",
            "Deterministic historical variants include sN/ssN host aliases and middle/bmiddle/large/orignal paths over HTTP/HTTPS.",
            "Image requests carry the source blog post as Referer.",
            "No image bytes are committed."
        ],
    }
    REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2), flush=True)


if __name__ == "__main__":
    main()
