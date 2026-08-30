#!/usr/bin/env python3
"""Extract media-resource evidence from public pages related to 《齐鲁少年》.

Records image URLs and technical metadata only; third-party image bytes are never committed.
Image metadata requests run concurrently so dead/slow CDNs cannot serially block the job.
"""
from __future__ import annotations

import csv
import hashlib
import io
import json
import re
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from html.parser import HTMLParser
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "data" / "media_evidence.csv"
UA = "qilu-shaonian-electronic-archive-media/2.0 (+https://github.com/Evolution404/qilu-shaonian-electronic-archive)"
TIMEOUT = 20
MAX_BYTES = 20 * 1024 * 1024
WORKERS = 16

TARGETS = [
    ("2022_winter_combined_issue", "https://news.lznews.cn/edu/ttxw/202201/t20220121_9728731.html"),
    ("2023_issue_1616_page1", "https://www.sohu.com/a/720789304_121106991"),
    ("2026_frontpage_index", "https://mt.sohu.com/kindfeed/learning/20260623/50"),
    ("2021_yunzhan_catalog", "https://www.yunzhan365.com/newspapers/publications/qilushaonian.html"),
    ("2019_reader_tenth_anniversary", "https://qlsnreadship.wordpress.com/%E5%B0%8F%E8%AF%BB%E8%80%85%E7%BE%A4-%E5%8D%81%E5%91%A8%E5%B9%B4/"),
    ("2009_editor_submission", "https://blog.sina.cn/dpool/blog/s/blog_4c4fc7d90100el5d.html"),
    ("2011_issue_1000_editor_post", "https://blog.sina.com.cn/s/blog_4c4fc7d90100zv7h.html"),
]

MEDIA_ATTRS = ("src", "data-src", "data-original", "data-lazy-src", "data-actualsrc")


class MediaParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.urls: list[tuple[str, str, str]] = []

    def handle_starttag(self, tag: str, attrs):
        d = {k.lower(): (v or "") for k, v in attrs}
        tag = tag.lower()
        if tag in {"img", "source"}:
            for attr in MEDIA_ATTRS:
                if d.get(attr):
                    self.urls.append((tag, attr, d[attr]))
            if d.get("srcset"):
                for part in d["srcset"].split(","):
                    value = part.strip().split()[0] if part.strip() else ""
                    if value:
                        self.urls.append((tag, "srcset", value))
        elif tag == "meta":
            prop = (d.get("property") or d.get("name") or "").lower()
            if prop in {"og:image", "twitter:image", "twitter:image:src"} and d.get("content"):
                self.urls.append(("meta", prop, d["content"]))
        elif tag == "link":
            if "image_src" in d.get("rel", "").lower() and d.get("href"):
                self.urls.append(("link", "href", d["href"]))


def request(url: str, *, max_bytes: int | None = None):
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "*/*"})
    with urllib.request.urlopen(req, timeout=TIMEOUT) as response:
        headers = {k.lower(): v for k, v in response.headers.items()}
        final_url = response.geturl()
        data = response.read() if max_bytes is None else response.read(max_bytes + 1)
        if max_bytes is not None and len(data) > max_bytes:
            raise ValueError(f"resource exceeds {max_bytes} bytes")
        return data, headers, final_url


def extract_urls(page_url: str, html: str):
    parser = MediaParser()
    parser.feed(html)
    out = []
    seen = set()
    for tag, attr, raw in parser.urls:
        value = raw.strip().replace("&amp;", "&")
        if value.startswith("//"):
            value = "https:" + value
        resolved = urllib.parse.urljoin(page_url, value)
        if not resolved.startswith(("http://", "https://")) or resolved in seen:
            continue
        seen.add(resolved)
        out.append((tag, attr, resolved))
    return out


def image_metadata(url: str):
    try:
        data, headers, final_url = request(url, max_bytes=MAX_BYTES)
        ctype = headers.get("content-type", "").split(";", 1)[0].strip().lower()
        width = height = image_format = ""
        if ctype.startswith("image/") or data.startswith((b"\x89PNG", b"\xff\xd8\xff", b"GIF8", b"RIFF")):
            try:
                with Image.open(io.BytesIO(data)) as im:
                    width, height, image_format = str(im.width), str(im.height), (im.format or "")
            except Exception:
                pass
        return {
            "resolved_url": final_url,
            "http_status": "200",
            "content_type": ctype,
            "content_length": str(len(data)),
            "sha256": hashlib.sha256(data).hexdigest(),
            "width": width,
            "height": height,
            "image_format": image_format,
            "fetch_error": "",
        }
    except Exception as exc:
        return {
            "resolved_url": "", "http_status": "", "content_type": "", "content_length": "",
            "sha256": "", "width": "", "height": "", "image_format": "",
            "fetch_error": f"{type(exc).__name__}: {exc}",
        }


def parse_page(source_id: str, page_url: str):
    raw, headers, final_url = request(page_url)
    encoding = "utf-8"
    match = re.search(r"charset=([\w.-]+)", headers.get("content-type", ""), re.I)
    if match:
        encoding = match.group(1)
    try:
        html = raw.decode(encoding, "replace")
    except LookupError:
        html = raw.decode("utf-8", "replace")
    media = extract_urls(final_url, html)
    print(f"{source_id}: discovered {len(media)} media URLs", flush=True)
    return final_url, media


def main() -> int:
    page_errors = []
    candidates = []
    for source_id, page_url in TARGETS:
        try:
            final_url, media = parse_page(source_id, page_url)
            for tag, attr, media_url in media:
                candidates.append((source_id, page_url, final_url, tag, attr, media_url))
        except Exception as exc:
            page_errors.append({"source_id": source_id, "page_url": page_url, "error": f"{type(exc).__name__}: {exc}"})
            print(f"{source_id}: PAGE ERROR {exc}", flush=True)

    rows = []
    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        futures = {pool.submit(image_metadata, item[5]): item for item in candidates}
        total = len(futures)
        done = 0
        for future in as_completed(futures):
            done += 1
            source_id, page_url, final_url, tag, attr, media_url = futures[future]
            meta = future.result()
            rows.append({
                "source_id": source_id,
                "page_url": page_url,
                "page_resolved_url": final_url,
                "html_tag": tag,
                "html_attribute": attr,
                "media_url": media_url,
                **meta,
            })
            if meta["width"] or done % 25 == 0:
                print(f"media {done}/{total}: {source_id} {meta['width']}x{meta['height']} {media_url}", flush=True)

    rows.sort(key=lambda r: (r["source_id"], r["media_url"]))
    fields = [
        "source_id", "page_url", "page_resolved_url", "html_tag", "html_attribute",
        "media_url", "resolved_url", "http_status", "content_type", "content_length",
        "sha256", "width", "height", "image_format", "fetch_error",
    ]
    with OUT.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerows(rows)

    large_portrait = sum(
        1 for r in rows
        if r["width"].isdigit() and r["height"].isdigit()
        and int(r["height"]) > int(r["width"])
        and int(r["height"]) >= 1000
    )
    report = {
        "target_count": len(TARGETS),
        "candidate_media_urls": len(candidates),
        "media_record_count": len(rows),
        "large_portrait_candidates": large_portrait,
        "page_errors": page_errors,
        "notes": [
            "Image bytes are inspected transiently for dimensions/hashes but are not committed.",
            "Large portrait images are triage candidates only; content must be verified before promotion to electronic_records.csv.",
        ],
    }
    (ROOT / "data" / "media_evidence_report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
