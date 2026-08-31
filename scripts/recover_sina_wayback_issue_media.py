#!/usr/bin/env python3
"""Recover media keys from historical Wayback snapshots of high-value Sina issue posts.

Current Sina HTML often rewrites legacy image references to a default placeholder. This probe
uses Wayback's closest historical HTML for issue-review / image-story posts, extracts only
sinaimg.cn media keys embedded in those archived pages, and probes deterministic large/orignal
variants. No third-party image bytes are committed.
"""
from __future__ import annotations

import csv
import hashlib
import json
import re
import urllib.parse
import urllib.request
from pathlib import Path
from PIL import Image
import io

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "data" / "repost_fullpage" / "sina_wayback_issue_media"
OUT.mkdir(parents=True, exist_ok=True)
UA = "qilu-shaonian-sina-wayback-media/1.0 (+https://github.com/Evolution404/qilu-shaonian-electronic-archive)"
TIMEOUT = 20
MAX_HTML = 8 * 1024 * 1024
MAX_MEDIA = 12 * 1024 * 1024

POSTS = [
    ("1053", "2012-11-28", "今天我评报（2012年11月27日）1053期", "http://blog.sina.com.cn/s/blog_4c4fc7d901019464.html"),
    ("1054", "2012-12-20", "1054期（2012年12月4日出版）看图说事", "http://blog.sina.com.cn/s/blog_4c4fc7d901019lzv.html"),
    ("1055", "2012-12-20", "1055期（2012年12月11日）看图说事", "http://blog.sina.com.cn/s/blog_4c4fc7d901019m01.html"),
    ("1057", "2012-12-24", "1057期（12月25日出版）看图说事", "http://blog.sina.com.cn/s/blog_4c4fc7d901019qm2.html"),
    ("1049", "2012-11-05", "2012年10月末（10月30日出版1049期）今天我评报", "http://blog.sina.com.cn/s/blog_4c4fc7d901018l0i.html"),
    ("1044", "2012-10-23", "2012年第1044期9月末今天我评报", "http://blog.sina.com.cn/s/blog_4c4fc7d901018amd.html"),
    ("1018", "2012-04-16", "2012年3月末 今天我评报", "http://blog.sina.com.cn/s/blog_4c4fc7d901013e27.html"),
    ("", "2012-01-10", "今天我评报", "http://blog.sina.com.cn/s/blog_4c4fc7d901011cp9.html"),
]

MEDIA_RE = re.compile(
    r'''(?i)https?://s\d+\.sinaimg\.cn/(?:middle|bmiddle|mw\d+|large|orignal)/[0-9a-z]+(?:&\d+)?'''
)


def request(url: str, max_bytes: int):
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "*/*"})
    with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
        raw = r.read(max_bytes + 1)
        if len(raw) > max_bytes:
            raise ValueError("response too large")
        return raw, r.geturl(), {k.lower(): v for k, v in r.headers.items()}


def closest(original: str, date: str):
    timestamp = date.replace("-", "")
    api = "https://archive.org/wayback/available?" + urllib.parse.urlencode({"url": original, "timestamp": timestamp})
    raw, _, _ = request(api, 2 * 1024 * 1024)
    data = json.loads(raw.decode("utf-8", "replace"))
    c = (data.get("archived_snapshots") or {}).get("closest") or {}
    if not c.get("available") or not c.get("url"):
        return "", ""
    u = re.sub(r"/web/(\d+)/", r"/web/\1id_/", c["url"], count=1)
    return u, c.get("timestamp", "")


def decode(raw: bytes, ctype: str):
    for enc in ("utf-8", "gb18030"):
        try:
            return raw.decode(enc)
        except Exception:
            pass
    return raw.decode("utf-8", "replace")


def variants(url: str):
    out = [("as_archived", url)]
    for kind in ("large", "orignal"):
        v = re.sub(r"/(?:middle|bmiddle|mw\d+|large|orignal)/", f"/{kind}/", url, count=1, flags=re.I)
        out.append((kind, v))
    uniq = []
    seen = set()
    for row in out:
        if row[1] not in seen:
            seen.add(row[1])
            uniq.append(row)
    return uniq


def probe_media(url: str):
    try:
        raw, final, headers = request(url, MAX_MEDIA)
        ctype = headers.get("content-type", "")
        width = height = fmt = ""
        try:
            im = Image.open(io.BytesIO(raw))
            width, height, fmt = str(im.width), str(im.height), im.format or ""
        except Exception:
            pass
        is_placeholder = "yes" if (width == "360" and height == "360") else "no"
        return final, ctype, str(len(raw)), hashlib.sha256(raw).hexdigest(), width, height, fmt, is_placeholder, ""
    except Exception as e:
        return "", "", "", "", "", "", "", "", f"{type(e).__name__}: {e}"[:1000]


def main():
    rows = []
    post_rows = []
    for issue, date, title, original in POSTS:
        try:
            snap, ts = closest(original, date)
            if not snap:
                post_rows.append({"issue": issue, "date": date, "title": title, "post_url": original, "snapshot": "", "timestamp": "", "media_refs": "0", "error": "no closest snapshot"})
                continue
            raw, final, h = request(snap, MAX_HTML)
            text = decode(raw, h.get("content-type", ""))
            urls = sorted(set(MEDIA_RE.findall(text)))
            post_rows.append({"issue": issue, "date": date, "title": title, "post_url": original, "snapshot": final, "timestamp": ts, "media_refs": str(len(urls)), "error": ""})
            for source in urls:
                for variant, candidate in variants(source):
                    final_m, ctype, size, sha, width, height, fmt, placeholder, error = probe_media(candidate)
                    rows.append({
                        "issue": issue, "post_date": date, "post_title": title, "post_url": original,
                        "snapshot_timestamp": ts, "snapshot_url": final, "source_media_url": source,
                        "variant": variant, "candidate_url": candidate, "resolved_url": final_m,
                        "content_type": ctype, "bytes": size, "sha256": sha, "width": width,
                        "height": height, "image_format": fmt, "is_placeholder": placeholder,
                        "portrait_document_geometry": "yes" if width and height and int(height) > int(width) * 1.25 and int(height) >= 900 else "no",
                        "error": error,
                    })
        except Exception as e:
            post_rows.append({"issue": issue, "date": date, "title": title, "post_url": original, "snapshot": "", "timestamp": "", "media_refs": "0", "error": f"{type(e).__name__}: {e}"[:1000]})

    with (OUT / "posts.csv").open("w", newline="", encoding="utf-8") as f:
        fields = ["issue", "date", "title", "post_url", "snapshot", "timestamp", "media_refs", "error"]
        w = csv.DictWriter(f, fieldnames=fields); w.writeheader(); w.writerows(post_rows)
    with (OUT / "media.csv").open("w", newline="", encoding="utf-8") as f:
        fields = ["issue", "post_date", "post_title", "post_url", "snapshot_timestamp", "snapshot_url", "source_media_url", "variant", "candidate_url", "resolved_url", "content_type", "bytes", "sha256", "width", "height", "image_format", "is_placeholder", "portrait_document_geometry", "error"]
        w = csv.DictWriter(f, fieldnames=fields); w.writeheader(); w.writerows(rows)

    report = {
        "target_posts": len(POSTS),
        "posts_with_wayback_snapshot": sum(bool(r["snapshot"]) for r in post_rows),
        "posts_with_media_refs": sum(int(r["media_refs"] or 0) > 0 for r in post_rows),
        "raw_media_refs": sum(int(r["media_refs"] or 0) for r in post_rows),
        "media_variant_rows": len(rows),
        "reachable_nonplaceholder_images": sum(bool(r["sha256"]) and r["is_placeholder"] == "no" for r in rows),
        "portrait_document_geometry": sum(r["portrait_document_geometry"] == "yes" and r["is_placeholder"] == "no" for r in rows),
        "notes": [
            "Historical Wayback HTML is used to recover media keys that current Sina HTML may rewrite.",
            "Geometry is triage only; any document candidate still requires visual/OCR verification.",
            "No image bytes are committed.",
        ],
    }
    (OUT / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2), flush=True)


if __name__ == "__main__":
    main()
