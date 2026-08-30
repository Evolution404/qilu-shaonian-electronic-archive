#!/usr/bin/env python3
"""Extract media-resource evidence from public pages related to 《齐鲁少年》.

The script records image URLs and technical metadata only. It does not commit third-party
image bytes. This makes hidden/orphaned newspaper-page images discoverable without turning
the repository into an uncontrolled mirror.
"""
from __future__ import annotations

import csv
import hashlib
import io
import json
import re
import urllib.parse
import urllib.request
from html.parser import HTMLParser
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "data" / "media_evidence.csv"
UA = "qilu-shaonian-electronic-archive-media/1.0 (+https://github.com/Evolution404/qilu-shaonian-electronic-archive)"
TIMEOUT = 30
MAX_BYTES = 20 * 1024 * 1024

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
URL_RE = re.compile(r"https?://[^\s\"'<>]+", re.I)


class MediaParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.urls: list[tuple[str, str, str]] = []

    def handle_starttag(self, tag: str, attrs):
        d = {k.lower(): (v or "") for k, v in attrs}
        if tag.lower() in {"img", "source"}:
            for attr in MEDIA_ATTRS:
                if d.get(attr):
                    self.urls.append((tag.lower(), attr, d[attr]))
            if d.get("srcset"):
                for part in d["srcset"].split(","):
                    value = part.strip().split()[0] if part.strip() else ""
                    if value:
                        self.urls.append((tag.lower(), "srcset", value))
        if tag.lower() == "meta":
            prop = (d.get("property") or d.get("name") or "").lower()
            if prop in {"og:image", "twitter:image", "twitter:image:src"} and d.get("content"):
                self.urls.append(("meta", prop, d["content"]))
        if tag.lower() == "link":
            rel = d.get("rel", "").lower()
            if "image_src" in rel and d.get("href"):
                self.urls.append(("link", "href", d["href"]))


def request(url: str, *, max_bytes: int | None = None) -> tuple[bytes, dict[str, str], str]:
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "*/*"})
    with urllib.request.urlopen(req, timeout=TIMEOUT) as response:
        headers = {k.lower(): v for k, v in response.headers.items()}
        final_url = response.geturl()
        if max_bytes is None:
            data = response.read()
        else:
            data = response.read(max_bytes + 1)
            if len(data) > max_bytes:
                raise ValueError(f"resource exceeds {max_bytes} bytes")
        return data, headers, final_url


def extract_urls(page_url: str, html: str) -> list[tuple[str, str, str]]:
    parser = MediaParser()
    parser.feed(html)
    out: list[tuple[str, str, str]] = []
    seen: set[str] = set()
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
        width = height = ""
        image_format = ""
        if ctype.startswith("image/") or data.startswith((b"\x89PNG", b"\xff\xd8\xff", b"GIF8", b"RIFF")):
            try:
                with Image.open(io.BytesIO(data)) as im:
                    width, height = str(im.width), str(im.height)
                    image_format = im.format or ""
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
            "resolved_url": "",
            "http_status": "",
            "content_type": "",
            "content_length": "",
            "sha256": "",
            "width": "",
            "height": "",
            "image_format": "",
            "fetch_error": f"{type(exc).__name__}: {exc}",
        }


def main() -> int:
    rows = []
    page_errors = []
    for source_id, page_url in TARGETS:
        try:
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
            print(f"{source_id}: {len(media)} media URLs", flush=True)
            for tag, attr, media_url in media:
                meta = image_metadata(media_url)
                rows.append({
                    "source_id": source_id,
                    "page_url": page_url,
                    "page_resolved_url": final_url,
                    "html_tag": tag,
                    "html_attribute": attr,
                    "media_url": media_url,
                    **meta,
                })
        except Exception as exc:
            page_errors.append({"source_id": source_id, "page_url": page_url, "error": f"{type(exc).__name__}: {exc}"})
            print(f"{source_id}: PAGE ERROR {exc}", flush=True)

    fields = [
        "source_id", "page_url", "page_resolved_url", "html_tag", "html_attribute",
        "media_url", "resolved_url", "http_status", "content_type", "content_length",
        "sha256", "width", "height", "image_format", "fetch_error",
    ]
    with OUT.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerows(rows)

    report = {
        "target_count": len(TARGETS),
        "media_record_count": len(rows),
        "page_errors": page_errors,
        "notes": [
            "Image bytes are inspected transiently for metadata and hashes but are not committed.",
            "Large dimensions/portrait orientation are only triage signals; visual/content verification is still required before promotion to electronic_records.csv.",
        ],
    }
    (ROOT / "data" / "media_evidence_report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
