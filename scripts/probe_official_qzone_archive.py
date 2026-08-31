#!/usr/bin/env python3
"""Probe the archived official 《齐鲁少年》 QQ空间 (857087447).

The 2009 editor-blog submission guide explicitly published 857087447.qzone.qq.com as
the paper's QQ空间.  This script inventories Wayback captures of that official space,
replays a bounded set of archived HTML pages, and extracts historical QQ/Qzone media
URLs.  It commits metadata only; media bytes are not stored.
"""
from __future__ import annotations

import csv
import json
import re
import time
from pathlib import Path
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "data" / "qzone_857087447"
OUT.mkdir(parents=True, exist_ok=True)
S = requests.Session()
S.headers.update({"User-Agent": "qilu-shaonian-qzone-archive/1.0"})
CDX = "https://web.archive.org/cdx/search/cdx"
TARGETS = [
    "857087447.qzone.qq.com/*",
    "user.qzone.qq.com/857087447/*",
    "qzone.qq.com/857087447/*",
]
MEDIA_RE = re.compile(r"(?i)(?:qpic\.cn|qlogo\.cn|photo\.store\.qq\.com|qzonestyle\.gtimg\.cn|qq\.com/.+\.(?:jpe?g|png|gif|bmp))(?:[^\"'<>\s]*)")
ISSUE_RE = re.compile(r"(?:第\s*)?(\d{3,4})\s*期")
KEY_RE = re.compile(r"齐鲁少年|版面|样报|电子版|电子报|评报|合刊|征稿|一版|二版|三版|四版")
IA_MARKERS = ("Wayback Machine Keep the news", "Search the history of more than", "Internet Archive logo")


def query(pattern):
    params = [
        ("url", pattern), ("from", "2009"), ("to", "2013"), ("output", "json"),
        ("fl", "timestamp,original,statuscode,mimetype,digest"), ("filter", "statuscode:200"),
        ("collapse", "urlkey"),
    ]
    last = ""
    for n in range(4):
        try:
            r = S.get(CDX, params=params, timeout=(15, 60))
            if r.status_code in (429, 500, 502, 503, 504):
                raise requests.HTTPError(f"retryable status {r.status_code}")
            r.raise_for_status(); data = r.json()
            if not data:
                return [], ""
            head = data[0]
            return [dict(zip(head, x)) for x in data[1:]], ""
        except Exception as e:
            last = f"{type(e).__name__}: {e}"; time.sleep(2 ** n)
    return [], last


def fetch_replay(row):
    u = f"https://web.archive.org/web/{row['timestamp']}id_/{row['original']}"
    last = ""
    for n in range(3):
        try:
            r = S.get(u, timeout=(15, 45)); r.raise_for_status()
            text = r.content.decode("utf-8", "replace")
            if text.count("�") > max(10, len(text) // 100):
                text = r.content.decode("gb18030", "replace")
            plain = re.sub(r"\s+", " ", BeautifulSoup(text, "html.parser").get_text(" ", strip=True))
            if any(x in plain for x in IA_MARKERS):
                return "", u, "wayback_landing"
            return text, u, ""
        except Exception as e:
            last = f"{type(e).__name__}: {e}"; time.sleep(1 + n)
    return "", u, last


def write_csv(path, rows, fields):
    with path.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore"); w.writeheader(); w.writerows(rows)


def main():
    inventory, errors = [], []
    for target in TARGETS:
        rows, err = query(target)
        for r in rows:
            r["query_target"] = target
        inventory.extend(rows)
        if err:
            errors.append({"stage": "cdx", "target": target, "error": err})
        print(target, len(rows), err[:100], flush=True)
        time.sleep(1)

    uniq = {}
    for r in inventory:
        uniq[(r.get("timestamp", ""), r.get("original", ""))] = r
    inventory = sorted(uniq.values(), key=lambda r: (r.get("timestamp", ""), r.get("original", "")))[:1000]

    # Prefer HTML-ish pages and URLs carrying likely content semantics; cap replay cost.
    candidates = [r for r in inventory if "html" in r.get("mimetype", "").lower() or not re.search(r"(?i)\.(?:css|js|gif|png|jpe?g|ico|swf)(?:\?|$)", r.get("original", ""))]
    candidates.sort(key=lambda r: (0 if re.search(r"(?i)blog|photo|album|index|main|home", r.get("original", "")) else 1, r.get("timestamp", "")))
    candidates = candidates[:80]

    pages, media = [], []
    for n, row in enumerate(candidates, 1):
        text, replay, err = fetch_replay(row)
        plain = ""
        refs = []
        if text:
            soup = BeautifulSoup(text, "html.parser")
            plain = re.sub(r"\s+", " ", soup.get_text(" ", strip=True))
            blobs = [text.replace("\\/", "/")]
            for blob in blobs:
                refs.extend(m.group(0) for m in MEDIA_RE.finditer(blob))
            for tag in soup.find_all(["img", "a"], src=True):
                refs.append(urljoin(row["original"], tag.get("src", "")))
            for tag in soup.find_all("a", href=True):
                h = tag.get("href", "")
                if re.search(r"(?i)qpic|qlogo|photo\.store\.qq|\.(?:jpe?g|png|gif)(?:\?|$)", h):
                    refs.append(urljoin(row["original"], h))
        refs = list(dict.fromkeys(x for x in refs if x and re.search(r"(?i)qpic|qlogo|photo\.store\.qq|qzonestyle|\.(?:jpe?g|png|gif)(?:\?|$)", x)))
        issues = sorted(set(ISSUE_RE.findall(plain)), key=int) if plain else []
        hits = [k for k in ("齐鲁少年", "版面", "样报", "电子版", "电子报", "评报", "合刊", "征稿") if k in plain]
        pages.append({
            "timestamp": row.get("timestamp", ""), "original": row.get("original", ""), "archive_url": replay,
            "mimetype": row.get("mimetype", ""), "issue_numbers": "|".join(issues), "keyword_hits": "|".join(hits),
            "media_refs": len(refs), "excerpt": plain[:1500], "error": err,
        })
        for ref in refs:
            media.append({"parent_original": row.get("original", ""), "parent_timestamp": row.get("timestamp", ""), "media_url": ref})
        if n % 10 == 0:
            print("replay", n, "/", len(candidates), "media", len(media), flush=True)

    media_uniq = {}
    for r in media:
        media_uniq[r["media_url"]] = r
    media = list(media_uniq.values())
    write_csv(OUT / "inventory.csv", inventory, ["query_target", "timestamp", "original", "statuscode", "mimetype", "digest"])
    write_csv(OUT / "pages.csv", pages, ["timestamp", "original", "archive_url", "mimetype", "issue_numbers", "keyword_hits", "media_refs", "excerpt", "error"])
    write_csv(OUT / "media_refs.csv", media, ["parent_original", "parent_timestamp", "media_url"])
    write_csv(OUT / "errors.csv", errors, ["stage", "target", "error"])
    report = {
        "official_qzone_uin": "857087447",
        "cdx_targets": TARGETS,
        "inventory_rows": len(inventory),
        "cdx_errors": len(errors),
        "pages_replayed": len(pages),
        "pages_with_qilu_keywords": sum(bool(r["keyword_hits"]) for r in pages),
        "pages_with_issue_numbers": sum(bool(r["issue_numbers"]) for r in pages),
        "unique_media_refs": len(media),
        "notes": [
            "The UIN/space is sourced from the verified 2009 editor-blog submission guide.",
            "Only archive metadata, text excerpts and media URLs are committed.",
            "QQ media URLs require separate content verification before promotion as newspaper pages."
        ],
    }
    (OUT / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2), flush=True)


if __name__ == "__main__":
    main()
