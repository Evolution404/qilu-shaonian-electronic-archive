#!/usr/bin/env python3
"""Reverse-link qlsn.com 2011-2012 archived media to their historical parent pages.

The domain identity timeline verifies qlsn.com as the 《齐鲁少年》 site through 2012-09.
This script takes archived ``upload/newsImg`` media whose path date falls inside that verified
period, scans only already-inventoried Wayback HTML snapshots for exact media basenames, and
records compact parent-page evidence (title, issue/page hints, short relevant excerpt).

No full page HTML or media bytes are committed.
"""
from __future__ import annotations

import csv
import html
import json
import re
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from html.parser import HTMLParser
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MEDIA = ROOT / "data" / "fullpage_backup" / "inspected_media_candidates.csv"
INVENTORY = ROOT / "data" / "archive_crawl" / "wayback_urls.csv"
OUTDIR = ROOT / "data" / "qlsn_media_parents"
OUTDIR.mkdir(parents=True, exist_ok=True)
UA = "qilu-shaonian-media-parent-recovery/1.0 (+https://github.com/Evolution404/qilu-shaonian-electronic-archive)"
TIMEOUT = 18
MAX_HTML = 4 * 1024 * 1024
WORKERS = 14

PATH_DATE = re.compile(r"/upload/newsImg/(20\d{2})-(\d{2})/", re.I)
ISSUE_RE = re.compile(r"(?:第\s*([0-9０-９]{2,5})\s*期|([0-9０-９]{2,5})\s*期)")
PAGE_RE = re.compile(r"(?:第\s*([A-DＡ-Ｄ0-9０-９]{1,3})\s*版|([A-DＡ-Ｄ][0-9０-９]?)\s*版|头版|一版|二版|三版|四版)")
QLSN_RE = re.compile(r"齐鲁少年|齐鲁少年报|齐鲁少年编辑部")


class P(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.in_title = False
        self.title = []
        self.text = []

    def handle_starttag(self, tag, attrs):
        if tag.lower() == "title":
            self.in_title = True

    def handle_endtag(self, tag):
        if tag.lower() == "title":
            self.in_title = False

    def handle_data(self, data):
        s = " ".join(data.split())
        if not s:
            return
        if self.in_title:
            self.title.append(s)
        self.text.append(s)


def decode(raw: bytes, ctype: str = ""):
    m = re.search(r"charset=([\w.-]+)", ctype, re.I)
    encs = ([m.group(1)] if m else []) + ["utf-8", "gb18030"]
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


def fetch(url: str):
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "text/html,*/*;q=0.5"})
    with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
        body = r.read(MAX_HTML + 1)
        if len(body) > MAX_HTML:
            raise ValueError("HTML too large")
        return body, r.geturl(), {k.lower(): v for k, v in r.headers.items()}


def target_media():
    rows = []
    with MEDIA.open(newline="", encoding="utf-8") as f:
        for r in csv.DictReader(f):
            if r.get("source") != "qlsn_bare":
                continue
            original = r.get("original", "")
            m = PATH_DATE.search(original)
            if not m:
                continue
            year, month = int(m.group(1)), int(m.group(2))
            if year < 2011 or year > 2012 or (year == 2012 and month > 9):
                continue
            basename = original.rsplit("/", 1)[-1]
            rows.append(
                {
                    "media_original": original,
                    "media_archive_url": r.get("archive_url", ""),
                    "media_snapshot_timestamp": r.get("timestamp", ""),
                    "media_sha256": r.get("sha256", ""),
                    "width": r.get("width", ""),
                    "height": r.get("height", ""),
                    "basename": basename,
                    "path_year": str(year),
                    "path_month": f"{month:02d}",
                }
            )
    uniq = {}
    for r in rows:
        uniq[r["media_original"].lower()] = r
    return list(uniq.values())


def html_inventory():
    rows = []
    with INVENTORY.open(newline="", encoding="utf-8") as f:
        for r in csv.DictReader(f):
            original = (r.get("original") or "").lower()
            mime = (r.get("mimetype") or "").lower()
            if "qlsn.com" not in original or "text/html" not in mime:
                continue
            if not r.get("archive_url"):
                continue
            # Do not scan obvious media/download endpoints as parent pages.
            if any(x in original for x in ("/upload/", ".jpg", ".jpeg", ".png", ".gif", ".pdf")):
                continue
            rows.append(r)
    uniq = {}
    for r in rows:
        key = (r.get("original", ""), r.get("timestamp", ""))
        uniq[key] = r
    return list(uniq.values())


def scan_page(inv: dict, targets: list[dict]):
    found = []
    err = ""
    try:
        raw, final, h = fetch(inv["archive_url"])
        source = decode(raw, h.get("content-type", ""))
        low = source.lower()
        hit_targets = [t for t in targets if t["basename"].lower() in low]
        if not hit_targets:
            return [], ""
        p = P()
        p.feed(source)
        visible = html.unescape(" ".join(p.text))
        title = " ".join(p.title)[:500]
        issues = []
        for a, b in ISSUE_RE.findall(visible):
            issues.append(a or b)
        issues = list(dict.fromkeys(issues))[:30]
        pages = []
        for a, b in PAGE_RE.findall(visible):
            pages.append(a or b)
        pages = list(dict.fromkeys(pages))[:20]
        qhits = len(QLSN_RE.findall(visible))
        for t in hit_targets:
            base = t["basename"]
            pos = low.find(base.lower())
            excerpt_source = source[max(0, pos - 700) : pos + len(base) + 1000]
            excerpt = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", html.unescape(excerpt_source))).strip()
            found.append(
                {
                    **t,
                    "parent_original": inv.get("original", ""),
                    "parent_snapshot_timestamp": inv.get("timestamp", ""),
                    "parent_archive_url": final,
                    "parent_title": title,
                    "qilu_shaonian_hits": str(qhits),
                    "issue_hints": "|".join(issues),
                    "page_hints": "|".join(pages),
                    "reference_excerpt": excerpt[:1800],
                    "classification": (
                        "strong_qilu_parent" if qhits > 0 and (issues or pages) else
                        "qilu_parent" if qhits > 0 else
                        "identity_unconfirmed_parent"
                    ),
                    "error": "",
                }
            )
    except Exception as exc:
        err = f"{type(exc).__name__}: {exc}"
    return found, err


def main():
    targets = target_media()
    pages = html_inventory()
    print(f"target media={len(targets)} inventoried html snapshots={len(pages)}", flush=True)
    matches = []
    errors = []
    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        futures = {pool.submit(scan_page, p, targets): p for p in pages}
        for n, fut in enumerate(as_completed(futures), 1):
            rows, err = fut.result()
            matches.extend(rows)
            if err:
                p = futures[fut]
                errors.append({"parent_original": p.get("original", ""), "archive_url": p.get("archive_url", ""), "error": err})
            for r in rows:
                print("PARENT HIT", r["classification"], r["basename"], r["parent_title"], flush=True)
            if n % 100 == 0:
                print("progress", n, "/", len(futures), "matches", len(matches), flush=True)

    # Dedup repeated Wayback captures of the same parent/media pair, preferring strong identity.
    rank = {"strong_qilu_parent": 3, "qilu_parent": 2, "identity_unconfirmed_parent": 1}
    uniq = {}
    for r in matches:
        key = (r["media_original"].lower(), r["parent_original"].lower())
        old = uniq.get(key)
        if old is None or rank[r["classification"]] > rank[old["classification"]]:
            uniq[key] = r
    matches = list(uniq.values())
    matches.sort(key=lambda r: (-rank[r["classification"]], r["path_year"], r["path_month"], r["media_original"], r["parent_original"]))

    fields = [
        "media_original", "media_archive_url", "media_snapshot_timestamp", "media_sha256", "width", "height",
        "basename", "path_year", "path_month", "parent_original", "parent_snapshot_timestamp", "parent_archive_url",
        "parent_title", "qilu_shaonian_hits", "issue_hints", "page_hints", "reference_excerpt", "classification", "error",
    ]
    with (OUTDIR / "parent_matches.csv").open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerows(matches)
    with (OUTDIR / "errors.csv").open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=["parent_original", "archive_url", "error"])
        w.writeheader()
        w.writerows(errors[:1000])

    report = {
        "target_media": len(targets),
        "inventoried_html_snapshots": len(pages),
        "unique_parent_matches": len(matches),
        "strong_qilu_parent_matches": sum(r["classification"] == "strong_qilu_parent" for r in matches),
        "qilu_parent_matches": sum(r["classification"] == "qilu_parent" for r in matches),
        "unconfirmed_parent_matches": sum(r["classification"] == "identity_unconfirmed_parent" for r in matches),
        "fetch_errors": len(errors),
        "matched_media": len({r["media_original"] for r in matches}),
        "notes": [
            "Media path dates are not publication dates.",
            "A parent match proves HTML reference association; content/issue verification is still required before promoting a newspaper page.",
            "qlsn.com root identity is independently verified through 2012-09, but each parent page remains evidence-scoped.",
        ],
    }
    (OUTDIR / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2), flush=True)


if __name__ == "__main__":
    main()
