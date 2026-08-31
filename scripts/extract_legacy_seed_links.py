#!/usr/bin/env python3
"""Map all internal links from verified historical qlsn.com snapshots.

This is a navigation-recovery tool. It preserves anchor text and original target paths so
older routes (including 2001-era links) can be studied even when they do not match the
2006/2007 ASP route conventions.
"""
from __future__ import annotations

import csv
import html
import re
import time
import urllib.parse
import urllib.request
from html.parser import HTMLParser
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "data" / "legacy_recovery" / "legacy_seed_links_all.csv"
OUT.parent.mkdir(parents=True, exist_ok=True)
UA = "qilu-shaonian-electronic-archive/seed-map-1.0 (+https://github.com/Evolution404/qilu-shaonian-electronic-archive)"

SEEDS = [
    ("2004_home", "https://web.archive.org/web/20040716162949id_/http://www.qlsn.com/", "http://www.qlsn.com/"),
    ("2004_about", "https://web.archive.org/web/20040804133449id_/http://www.qlsn.com/page/P1.htm", "http://www.qlsn.com/page/P1.htm"),
    ("2007_home", "https://web.archive.org/web/20070623224142id_/http://www.qlsn.com/index.asp", "http://www.qlsn.com/index.asp"),
]


class P(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.current = None
        self.text = []
        self.links = []
    def handle_starttag(self, tag, attrs):
        if tag.lower() == "a":
            d = {k.lower(): (v or "") for k, v in attrs}
            if d.get("href"):
                self.current = d["href"]; self.text = []
    def handle_data(self, data):
        if self.current is not None:
            t = " ".join(data.split())
            if t: self.text.append(t)
    def handle_endtag(self, tag):
        if tag.lower() == "a" and self.current is not None:
            self.links.append((self.current, " ".join(self.text).strip()))
            self.current = None; self.text = []


def fetch(url):
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "text/html,*/*"})
    with urllib.request.urlopen(req, timeout=15) as r:
        return r.read()


def decode(raw):
    cands = []
    for enc in ("utf-8", "gb18030", "big5"):
        text = raw.decode(enc, errors="replace")
        cands.append((text.count("\ufffd"), enc, text))
    return min(cands, key=lambda x: x[0])[1:]


def unwrap_wayback(href):
    m = re.search(r"/web/\d+(?:id_)?/(https?://.+)$", href)
    return m.group(1) if m else href


def classify(url):
    p = urllib.parse.urlsplit(url)
    path = p.path.lower()
    q = p.query.lower()
    if re.search(r"article|news|announce|pic|content|page", path): return "content_like"
    if re.search(r"(?:id|no|page|date)=", q): return "parameterized"
    if path.endswith((".jpg", ".jpeg", ".png", ".gif", ".bmp")): return "image"
    if path.endswith((".htm", ".html", ".asp", ".aspx", "/")): return "html_or_route"
    return "other"


def main():
    rows = []
    errors = []
    for seed_id, archive_url, original_base in SEEDS:
        try:
            raw = fetch(archive_url); enc, text = decode(raw)
            p = P(); p.feed(text)
            for order, (href, anchor) in enumerate(p.links, start=1):
                href = html.unescape(unwrap_wayback(href.strip()))
                target = urllib.parse.urljoin(original_base, href)
                parsed = urllib.parse.urlsplit(target)
                host = (parsed.hostname or "").lower()
                internal = host in {"qlsn.com", "www.qlsn.com"} or (not host and not parsed.scheme)
                if internal and not host:
                    target = urllib.parse.urljoin(original_base, target)
                    host = (urllib.parse.urlsplit(target).hostname or "").lower()
                rows.append({
                    "seed_id": seed_id, "seed_archive_url": archive_url, "seed_encoding": enc,
                    "order": order, "anchor_text": anchor, "raw_href": href, "resolved_original_url": target,
                    "host": host, "internal_qlsn_com": "yes" if host in {"qlsn.com", "www.qlsn.com"} else "no",
                    "route_class": classify(target),
                })
        except Exception as exc:
            errors.append((seed_id, f"{type(exc).__name__}: {exc}"))
    fields = ["seed_id", "seed_archive_url", "seed_encoding", "order", "anchor_text", "raw_href", "resolved_original_url", "host", "internal_qlsn_com", "route_class"]
    with OUT.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields); w.writeheader(); w.writerows(rows)
    print(f"mapped {len(rows)} links; errors={errors}", flush=True)
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
