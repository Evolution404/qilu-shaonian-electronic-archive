#!/usr/bin/env python3
"""Verify whether archived qlsn.com snapshots belong to historical 《齐鲁少年》.

This script does NOT promote a URL to an electronic edition. It reads the Wayback URL
inventory, fetches a bounded set of archived HTML pages from qlsn.com/www.qlsn.com,
extracts titles/metadata/visible-text keyword hits, and emits an image-resource candidate
inventory. The result is evidence for human/automated classification in later steps.
"""
from __future__ import annotations

import csv
import datetime as dt
import html
import json
import re
import time
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from html.parser import HTMLParser
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "data" / "archive_crawl" / "wayback_urls.csv"
OUT = ROOT / "data" / "site_verification"
OUT.mkdir(parents=True, exist_ok=True)
UA = "qilu-shaonian-electronic-archive/site-verifier-1.1 (+https://github.com/Evolution404/qilu-shaonian-electronic-archive)"
TIMEOUT = 12
WORKERS = 10
MAX_HTML = 30

TARGET_HOSTS = {"qlsn.com", "www.qlsn.com"}
IDENTITY_KEYWORDS = [
    "齐鲁少年", "齐鲁少年报", "山东少年报", "山东青年报", "鲁青", "少年报",
    "小记者", "少先队", "编辑部", "报社", "投稿", "读者", "校园",
]
HIGH_VALUE_PATH = re.compile(
    r"(?:^/$|index\.(?:asp|aspx|htm|html)$|article_view\.asp|news_view\.asp|"
    r"announce_(?:list|view)\.asp|downloads_(?:list|view)\.asp|pic_view\.asp|"
    r"lianxi\.asp|/page/p\d+\.htm|sys_webs/news\.aspx)", re.I
)
IMAGE_HINT = re.compile(r"(?:paper|page|bao|newspaper|newsimg|qilu|shaonian|少年|报|版)", re.I)
DATE_PATH = re.compile(r"/(20\d{2})[-_/](0?[1-9]|1[0-2])(?:[-_/]|$)")


class Extractor(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.in_title = False
        self.skip_depth = 0
        self.title_parts: list[str] = []
        self.text_parts: list[str] = []
        self.meta: dict[str, str] = {}
        self.links: list[str] = []
        self.images: list[str] = []

    def handle_starttag(self, tag, attrs):
        tag = tag.lower()
        attrs_d = {k.lower(): (v or "") for k, v in attrs}
        if tag == "title":
            self.in_title = True
        if tag in {"script", "style", "noscript"}:
            self.skip_depth += 1
        if tag == "meta":
            key = (attrs_d.get("name") or attrs_d.get("property") or "").lower()
            content = attrs_d.get("content", "")
            if key and content:
                self.meta[key] = content
        elif tag == "a" and attrs_d.get("href"):
            self.links.append(attrs_d["href"])
        elif tag == "img":
            src = attrs_d.get("src") or attrs_d.get("data-src") or ""
            if src:
                self.images.append(src)

    def handle_endtag(self, tag):
        tag = tag.lower()
        if tag == "title":
            self.in_title = False
        if tag in {"script", "style", "noscript"} and self.skip_depth:
            self.skip_depth -= 1

    def handle_data(self, data):
        clean = " ".join(data.split())
        if not clean:
            return
        if self.in_title:
            self.title_parts.append(clean)
        if not self.skip_depth:
            self.text_parts.append(clean)


def decode_html(raw: bytes) -> tuple[str, str]:
    candidates = []
    for enc in ("utf-8", "gb18030", "big5"):
        try:
            text = raw.decode(enc, errors="replace")
            candidates.append((text.count("\ufffd"), enc, text))
        except Exception:
            pass
    if not candidates:
        return raw.decode("latin1", errors="replace"), "latin1"
    _, enc, text = min(candidates, key=lambda x: x[0])
    return text, enc


def fetch(url: str) -> tuple[bytes, str, str]:
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "text/html,*/*"})
    with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
        return r.read(), r.geturl(), r.headers.get("Content-Type", "")


def read_inventory() -> list[dict]:
    with SOURCE.open(newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def host_of(url: str) -> str:
    return (urllib.parse.urlparse(url).hostname or "").lower()


def page_score(row: dict) -> int:
    p = urllib.parse.urlparse(row["original"])
    path = p.path or "/"
    score = 0
    if path in {"/", "/index.asp", "/index.htm", "/index.html", "/webs/index.aspx"}:
        score += 100
    if HIGH_VALUE_PATH.search(path):
        score += 60
    if any(x in path.lower() for x in ("article", "news", "announce", "pic", "download", "lianxi", "page")):
        score += 20
    ts = row.get("timestamp", "")
    try:
        year = int(ts[:4])
        score += max(0, 2020 - year) // 2
    except Exception:
        pass
    return score


def verify_one(row: dict) -> dict:
    out = {
        "timestamp": row.get("timestamp", ""), "original": row.get("original", ""),
        "archive_url": row.get("archive_url", ""), "score": page_score(row),
        "http_status": "", "resolved_url": "", "content_type": "", "encoding": "",
        "title": "", "description": "", "keywords_meta": "", "identity_hits": "",
        "identity_hit_count": 0, "visible_text_excerpt": "", "link_count": 0,
        "image_count": 0, "fetch_error": "",
    }
    try:
        raw, final_url, content_type = fetch(row["archive_url"])
        text, enc = decode_html(raw)
        parser = Extractor()
        parser.feed(text)
        visible = html.unescape(" ".join(parser.text_parts))
        visible = re.sub(r"\s+", " ", visible).strip()
        title = " ".join(parser.title_parts).strip()
        combined = " ".join([title, parser.meta.get("description", ""), parser.meta.get("keywords", ""), visible])
        hits = [kw for kw in IDENTITY_KEYWORDS if kw in combined]
        out.update({
            "http_status": 200, "resolved_url": final_url, "content_type": content_type,
            "encoding": enc, "title": title[:500],
            "description": parser.meta.get("description", "")[:800],
            "keywords_meta": parser.meta.get("keywords", "")[:800],
            "identity_hits": "|".join(hits), "identity_hit_count": len(hits),
            "visible_text_excerpt": visible[:1200], "link_count": len(parser.links),
            "image_count": len(parser.images),
        })
    except Exception as exc:
        out["fetch_error"] = f"{type(exc).__name__}: {exc}"
    return out


def image_row(row: dict) -> dict:
    p = urllib.parse.urlparse(row["original"])
    path = p.path or ""
    m = DATE_PATH.search(path)
    year_month = f"{m.group(1)}-{int(m.group(2)):02d}" if m else ""
    low = path.lower()
    kind = "other_image"
    if "/newsimg/" in low:
        kind = "newsImg"
    elif "/kind/" in low:
        kind = "kind"
    elif "/link/" in low:
        kind = "link"
    elif "/images/" in low:
        kind = "site_image"
    score = 0
    if kind == "newsImg": score += 50
    if year_month: score += 20
    if IMAGE_HINT.search(path): score += 15
    if re.search(r"(?:^|/)(?:p\d+|page\d*|[a-d]?\d)\.(?:jpe?g|png|gif)$", low): score += 10
    return {
        "timestamp": row.get("timestamp", ""), "original": row.get("original", ""),
        "archive_url": row.get("archive_url", ""), "mimetype": row.get("mimetype", ""),
        "resource_kind": kind, "path_year_month": year_month, "candidate_score": score,
        "digest": row.get("digest", ""), "status": "candidate_requires_visual_verification",
    }


def write_csv(path: Path, rows: list[dict], fields: list[str]) -> None:
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        w.writeheader(); w.writerows(rows)


def main() -> int:
    started = time.monotonic()
    rows = read_inventory()
    target = [r for r in rows if host_of(r.get("original", "")) in TARGET_HOSTS]
    html_rows = [r for r in target if "html" in (r.get("mimetype") or "").lower()]
    image_rows = [r for r in target if (r.get("mimetype") or "").lower().startswith("image/")]
    html_rows.sort(key=lambda r: (-page_score(r), r.get("timestamp", ""), r.get("original", "")))
    selected = html_rows[:MAX_HTML]
    verified = []
    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        futures = {pool.submit(verify_one, r): r for r in selected}
        for future in as_completed(futures):
            result = future.result(); verified.append(result)
            print(f"verified {result['original']} title={result['title'][:80]!r} hits={result['identity_hits']} err={result['fetch_error']}", flush=True)
    verified.sort(key=lambda r: (-int(r["identity_hit_count"] or 0), -int(r["score"] or 0), r["original"]))
    images = [image_row(r) for r in image_rows]
    images.sort(key=lambda r: (-int(r["candidate_score"] or 0), r["original"]))
    page_fields = ["timestamp", "original", "archive_url", "score", "http_status", "resolved_url", "content_type", "encoding", "title", "description", "keywords_meta", "identity_hits", "identity_hit_count", "visible_text_excerpt", "link_count", "image_count", "fetch_error"]
    image_fields = ["timestamp", "original", "archive_url", "mimetype", "resource_kind", "path_year_month", "candidate_score", "digest", "status"]
    write_csv(OUT / "qlsn_com_pages.csv", verified, page_fields)
    write_csv(OUT / "qlsn_com_images.csv", images, image_fields)
    hits = [r for r in verified if int(r["identity_hit_count"] or 0) > 0]
    report = {
        "generated_at_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
        "elapsed_seconds": round(time.monotonic() - started, 2), "verifier_version": "1.1",
        "source_records": len(target), "html_records": len(html_rows), "html_checked": len(selected),
        "html_with_identity_keyword_hits": len(hits), "image_records": len(image_rows),
        "strongest_pages": [{"original": r["original"], "archive_url": r["archive_url"], "title": r["title"], "identity_hits": r["identity_hits"], "excerpt": r["visible_text_excerpt"][:300]} for r in hits[:20]],
        "notes": [
            "First pass is deliberately bounded to the highest-priority 30 HTML snapshots; deeper page batches follow only after site identity is established.",
            "Keyword hits are evidence, not automatic proof of an electronic newspaper edition.",
            "Image rows are candidates only until visual/page-level verification confirms a newspaper page or edition.",
        ],
    }
    (OUT / "qlsn_com_report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
