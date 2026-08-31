#!/usr/bin/env python3
"""Build an evidence-based identity timeline for historical qlsn.com snapshots.

The domain was the official 《齐鲁少年》 site in the mid-2000s but was later repurposed.
Media paths such as /upload/newsImg/2011-2014 therefore cannot be attributed merely from
filenames. This script samples Wayback's closest root snapshot quarterly, extracts only
small identity metadata (title, text markers, copyright strings), and classifies the site
identity without persisting full third-party pages.
"""
from __future__ import annotations

import csv
import html
import json
import re
import urllib.parse
import urllib.request
from html.parser import HTMLParser
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "data" / "qlsn_domain_timeline.csv"
REPORT = ROOT / "data" / "qlsn_domain_timeline_report.json"
UA = "qilu-shaonian-domain-timeline/1.0 (+https://github.com/Evolution404/qilu-shaonian-electronic-archive)"
TIMEOUT = 18
ROOT_URLS = ["http://www.qlsn.com/", "http://qlsn.com/"]
DATES = [f"{year}{month:02d}15" for year in range(2008, 2016) for month in (2, 5, 8, 11)]

MARKERS = {
    "qilu_shaonian": ["齐鲁少年", "齐鲁少年编辑部", "山东青年报"],
    "youth_center": ["青少年活动中心", "山东省青少年活动中心", "青少年宫"],
    "seo_or_unrelated": ["博彩", "娱乐城", "seo", "彩票", "赌博", "游戏"],
}


class TextParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.title = []
        self.text = []
        self.in_title = False

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


def get(url: str, limit=3 * 1024 * 1024):
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "text/html,application/json,*/*;q=0.5"})
    with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
        body = r.read(limit + 1)
        if len(body) > limit:
            raise ValueError("response too large")
        return body, r.geturl(), {k.lower(): v for k, v in r.headers.items()}


def available(url: str, stamp: str):
    api = "https://archive.org/wayback/available?" + urllib.parse.urlencode({"url": url, "timestamp": stamp})
    raw, _, _ = get(api, 1024 * 1024)
    data = json.loads(raw.decode("utf-8", "replace"))
    c = (data.get("archived_snapshots") or {}).get("closest") or {}
    if not c.get("available") or not c.get("url"):
        return "", ""
    u = re.sub(r"/web/(\d+)/", r"/web/\1id_/", c["url"], count=1)
    return u, c.get("timestamp", "")


def decode(raw: bytes, ctype: str):
    m = re.search(r"charset=([\w.-]+)", ctype or "", re.I)
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


def classify(text: str):
    counts = {group: sum(text.count(x) for x in values) for group, values in MARKERS.items()}
    if counts["qilu_shaonian"]:
        identity = "qilu_shaonian"
    elif counts["youth_center"]:
        identity = "youth_center"
    elif counts["seo_or_unrelated"]:
        identity = "seo_or_unrelated"
    else:
        identity = "unclear"
    return identity, counts


def one(root_url: str, requested: str):
    row = {
        "requested_date": requested,
        "root_url": root_url,
        "snapshot_timestamp": "",
        "snapshot_url": "",
        "title": "",
        "identity": "",
        "qilu_shaonian_hits": "0",
        "youth_center_hits": "0",
        "seo_or_unrelated_hits": "0",
        "identity_excerpt": "",
        "error": "",
    }
    try:
        snap, ts = available(root_url, requested)
        if not snap:
            row["identity"] = "no_snapshot"
            return row
        raw, final, h = get(snap)
        source = decode(raw, h.get("content-type", ""))
        p = TextParser()
        p.feed(source)
        visible = html.unescape(" ".join(p.text))
        identity, counts = classify(visible)
        relevant = []
        for marker in sum(MARKERS.values(), []):
            pos = visible.find(marker)
            if pos >= 0:
                relevant.append(visible[max(0, pos - 90) : pos + len(marker) + 160])
        row.update(
            {
                "snapshot_timestamp": ts,
                "snapshot_url": final,
                "title": " ".join(p.title)[:300],
                "identity": identity,
                "qilu_shaonian_hits": str(counts["qilu_shaonian"]),
                "youth_center_hits": str(counts["youth_center"]),
                "seo_or_unrelated_hits": str(counts["seo_or_unrelated"]),
                "identity_excerpt": " || ".join(relevant)[:1200],
            }
        )
    except Exception as exc:
        row["identity"] = "fetch_error"
        row["error"] = f"{type(exc).__name__}: {exc}"
    return row


def main():
    rows = []
    # Keep it serial and polite; availability calls are small and quarterly only.
    for requested in DATES:
        best = None
        for root in ROOT_URLS:
            r = one(root, requested)
            if best is None or (r["identity"] not in {"no_snapshot", "fetch_error", "unclear"} and best["identity"] in {"no_snapshot", "fetch_error", "unclear"}):
                best = r
            if r["identity"] not in {"no_snapshot", "fetch_error", "unclear"}:
                break
        rows.append(best)
        print(requested, best["snapshot_timestamp"], best["identity"], best["title"], flush=True)

    # Deduplicate identical closest snapshots reached by neighboring quarterly requests.
    uniq = []
    seen = set()
    for r in rows:
        key = (r["snapshot_timestamp"], r["identity"], r["root_url"])
        if r["snapshot_timestamp"] and key in seen:
            continue
        seen.add(key)
        uniq.append(r)

    fields = list(uniq[0].keys()) if uniq else [
        "requested_date", "root_url", "snapshot_timestamp", "snapshot_url", "title", "identity",
        "qilu_shaonian_hits", "youth_center_hits", "seo_or_unrelated_hits", "identity_excerpt", "error"
    ]
    with OUT.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerows(uniq)

    report = {
        "samples_requested": len(DATES),
        "unique_snapshots": len(uniq),
        "identity_counts": {k: sum(r["identity"] == k for r in uniq) for k in sorted({r["identity"] for r in uniq})},
        "last_qilu_shaonian_snapshot": max((r["snapshot_timestamp"] for r in uniq if r["identity"] == "qilu_shaonian"), default=""),
        "first_youth_center_snapshot": min((r["snapshot_timestamp"] for r in uniq if r["identity"] == "youth_center"), default=""),
        "notes": [
            "Closest-snapshot sampling establishes domain identity, not the creation date of each media object.",
            "Media from ambiguous transition periods still requires parent-page/content verification before attribution.",
        ],
    }
    REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2), flush=True)


if __name__ == "__main__":
    main()
