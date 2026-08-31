#!/usr/bin/env python3
"""Classify archive URL inventory into focused candidates for 《齐鲁少年》 research.

No row is automatically promoted into the verified electronic-edition table. This script
only creates a compact review queue from the much larger Wayback inventory.
"""
from __future__ import annotations

import csv
import re
import urllib.parse
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "data" / "archive_crawl" / "wayback_urls.csv"
OUT = ROOT / "data" / "archive_candidates"
OUT.mkdir(parents=True, exist_ok=True)

EXCLUDED_HOSTS = {"qlsn.cn", "www.qlsn.cn", "paper.cnssiot.cn"}
KNOWN_HOSTS = {
    "www.dzwww.com", "202.102.188.131", "szb.cnssiot.cn",
    "blog.sina.com.cn", "blog.sina.cn", "qlsnreadship.wordpress.com",
    "qlsnreadship.files.wordpress.com", "www.yunzhan365.com",
}
QLSN_COM_HOSTS = {"qlsn.com", "www.qlsn.com"}

ELECTRONIC_HINT = re.compile(
    r"(?:qilushaonian|shaonian|edition|paper|page|ban|yiban|erban|sanban|siban|"
    r"newsimg|pic(?:ture)?|upload|\.(?:jpe?g|png|gif|pdf)(?:$|\?))", re.I
)
DATE_HINT = re.compile(r"(?:20\d{2}[-_/]?(?:0?[1-9]|1[0-2])[-_/]?(?:0?[1-9]|[12]\d|3[01])?|20\d{2}[-_/](?:0?[1-9]|1[0-2]))")
CONTENT_HINT = re.compile(r"(?:article|news|announce|download|pic|page|content|edition|upload/newsimg)", re.I)


def host(url: str) -> str:
    return (urllib.parse.urlparse(url).hostname or "").lower()


def score(row: dict) -> tuple[int, str]:
    url = row.get("original", "")
    h = host(url)
    low = url.lower()
    s = 0
    reasons = []
    if h in KNOWN_HOSTS:
        s += 50
        reasons.append("known_source")
    if h in QLSN_COM_HOSTS:
        s += 25
        reasons.append("qlsn_com_unverified_identity")
    if ELECTRONIC_HINT.search(url):
        s += 25
        reasons.append("electronic_resource_hint")
    if DATE_HINT.search(url):
        s += 15
        reasons.append("date_in_url")
    if CONTENT_HINT.search(url):
        s += 15
        reasons.append("content_path_hint")
    mime = (row.get("mimetype") or "").lower()
    if mime.startswith("image/"):
        s += 15
        reasons.append("image_resource")
    elif "html" in mime:
        s += 5
        reasons.append("html_page")
    if "/upload/newsimg/" in low:
        s += 35
        reasons.append("newsImg_resource")
    if h in QLSN_COM_HOSTS and re.search(r"/(?:index|article_view|news_view|pic_view|page/|sys_webs/news)", low):
        s += 20
        reasons.append("legacy_site_content_route")
    return s, "|".join(reasons)


def main() -> int:
    with SRC.open(newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    out = []
    for row in rows:
        h = host(row.get("original", ""))
        if not h or h in EXCLUDED_HOSTS:
            continue
        s, reasons = score(row)
        if s < 30:
            continue
        status = "review"
        if h in QLSN_COM_HOSTS:
            status = "identity_verification_required"
        elif h in KNOWN_HOSTS:
            status = "source_known_content_verification_required"
        out.append({
            "candidate_score": s,
            "status": status,
            "host": h,
            "timestamp": row.get("timestamp", ""),
            "mimetype": row.get("mimetype", ""),
            "original": row.get("original", ""),
            "archive_url": row.get("archive_url", ""),
            "reasons": reasons,
            "digest": row.get("digest", ""),
        })
    out.sort(key=lambda r: (-int(r["candidate_score"]), r["host"], r["original"]))
    fields = ["candidate_score", "status", "host", "timestamp", "mimetype", "original", "archive_url", "reasons", "digest"]
    with (OUT / "wayback_candidates.csv").open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerows(out)
    print(f"archive candidates: {len(out)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
