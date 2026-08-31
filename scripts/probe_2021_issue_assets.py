#!/usr/bin/env python3
"""Minimal, provenance-first probe for the 2021-12-25 《齐鲁少年》 A1-A8 issue.

This deliberately avoids broad CDX crawling. It asks Wayback for the eight known edition
pages, extracts only newspaper Img assets and the Pagepdf link, then verifies those media
responses and records SHA-256. No third-party newspaper bytes are committed.
"""
from __future__ import annotations

import csv
import hashlib
import html
import json
import re
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "data" / "szb_2021_issue_probe"
OUT.mkdir(parents=True, exist_ok=True)

UA = "qilu-shaonian-2021-issue-probe/1.0 (+https://github.com/Evolution404/qilu-shaonian-electronic-archive)"
TIMEOUT = 18
MAX_PAGE = 8 * 1024 * 1024
MAX_MEDIA = 40 * 1024 * 1024
TS = "20220928010853"

EDITIONS = {
    "A1": ("326", "http://szb.cnssiot.cn/content/2021-12/25/edition326_A1.html"),
    "A2": ("333", "http://szb.cnssiot.cn/content/2021-12/25/edition333_A2.html"),
    "A3": ("327", "http://szb.cnssiot.cn/content/2021-12/25/edition327_A3.html"),
    "A4": ("328", "http://szb.cnssiot.cn/content/2021-12/25/edition328_A4.html"),
    "A5": ("329", "http://szb.cnssiot.cn/content/2021-12/25/edition329_A5.html"),
    "A6": ("330", "http://szb.cnssiot.cn/content/2021-12/25/edition330_A6.html"),
    "A7": ("331", "http://szb.cnssiot.cn/content/2021-12/25/edition331_A7.html"),
    "A8": ("332", "http://szb.cnssiot.cn/content/2021-12/25/edition332_A8.html"),
}

ROOT_SEED = (
    "A1",
    "326",
    "http://szb.cnssiot.cn/Img/2021/12/"
    "mobile202112245170816f8aaf438f9a1a9119831a2eab.jpg?editionid=326",
)

IMG_RE = re.compile(
    r'''(?i)(?:(?:https?:)?//szb\.cnssiot\.cn)?/?Img/[^"'<> \t\r\n]+?\.(?:jpe?g|png|pdf)(?:\?[^"'<> \t\r\n]*)?'''
)
PAGEPIC_RE = re.compile(r'''(?is)\bpagepic\s*=\s*["']([^"']+)["']''')
PDF_ANCHOR_RE = re.compile(
    r'''(?is)<a\b[^>]*href=["']([^"']+)["'][^>]*>(?:(?!</a>).){0,1000}?PDF原(?:版|面)(?:(?!</a>).){0,200}?</a>'''
)


def request(url: str, max_bytes: int):
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "*/*"})
    with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
        body = r.read(max_bytes + 1)
        if len(body) > max_bytes:
            raise ValueError(f"response exceeds {max_bytes} bytes")
        return body, r.geturl(), {k.lower(): v for k, v in r.headers.items()}


def decode(raw: bytes, ctype: str = ""):
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


def wayback_raw(original: str, timestamp: str = TS):
    return f"https://web.archive.org/web/{timestamp}id_/{original}"


def available(original: str):
    api = "https://archive.org/wayback/available?" + urllib.parse.urlencode(
        {"url": original, "timestamp": TS[:8]}
    )
    try:
        raw, _, _ = request(api, 2 * 1024 * 1024)
        data = json.loads(raw.decode("utf-8", "replace"))
        c = (data.get("archived_snapshots") or {}).get("closest") or {}
        if c.get("available") and c.get("url"):
            u = re.sub(r"/web/(\d+)/", r"/web/\1id_/", c["url"], count=1)
            return u, c.get("timestamp", "")
    except Exception:
        pass
    return "", ""


def normalize(base: str, value: str):
    v = html.unescape(value.strip())
    if v.startswith("//"):
        v = "http:" + v
    if v.lower().startswith("img/"):
        v = "/" + v
    u = urllib.parse.urljoin(base, v)
    p = urllib.parse.urlsplit(u)
    if p.netloc == "web.archive.org":
        m = re.search(r"/web/\d+(?:id_)?/(https?://.+)$", p.path + (("?" + p.query) if p.query else ""))
        if m:
            u = m.group(1)
    return u


def fetch_page(page: str, eid: str, original: str):
    attempts = [wayback_raw(original)]
    closest, _ = available(original)
    if closest and closest not in attempts:
        attempts.append(closest)
    errs = []
    for candidate in attempts:
        try:
            raw, final, h = request(candidate, MAX_PAGE)
            text = decode(raw, h.get("content-type", ""))
            if "<html" not in text.lower() and "<!doctype" not in text.lower():
                errs.append(f"{candidate}: non-html")
                continue
            assets = []
            for raw_url in IMG_RE.findall(text):
                assets.append((normalize(original, raw_url), "Img_reference"))
            for raw_url in PAGEPIC_RE.findall(text):
                u = normalize(original, raw_url)
                if "/img/" in u.lower():
                    if "editionid=" not in u.lower() and re.search(r"\.(?:jpe?g|png)$", u, re.I):
                        u += ("&" if "?" in u else "?") + f"editionid={eid}"
                    assets.append((u, "Pagepic"))
            for raw_url in PDF_ANCHOR_RE.findall(text):
                assets.append((normalize(original, raw_url), "Pagepdf"))
            rank = {"Pagepdf": 3, "Pagepic": 2, "Img_reference": 1}
            uniq = {}
            for u, how in assets:
                if not u.startswith(("http://", "https://")):
                    continue
                old = uniq.get(u)
                if old is None or rank[how] > rank[old]:
                    uniq[u] = how
            return {
                "page": page,
                "edition_id": eid,
                "original_url": original,
                "snapshot_url": final,
                "status": "recovered",
                "html_bytes": str(len(raw)),
                "asset_count": str(len(uniq)),
                "error": "",
            }, [(u, how) for u, how in uniq.items()]
        except Exception as e:
            errs.append(f"{candidate}: {type(e).__name__}: {e}")
    return {
        "page": page,
        "edition_id": eid,
        "original_url": original,
        "snapshot_url": "",
        "status": "missing",
        "html_bytes": "0",
        "asset_count": "0",
        "error": " | ".join(errs)[:3000],
    }, []


def verify_media(url: str):
    attempts = [wayback_raw(url)]
    closest, closest_ts = available(url)
    if closest and closest not in attempts:
        attempts.append(closest)
    errs = []
    for candidate in attempts:
        try:
            raw, final, h = request(candidate, MAX_MEDIA)
            ctype = h.get("content-type", "").lower()
            is_pdf = raw.startswith(b"%PDF-")
            is_img = raw.startswith(b"\xff\xd8\xff") or raw.startswith(b"\x89PNG\r\n\x1a\n")
            if not (is_pdf or is_img or ctype.startswith("image/") or "pdf" in ctype):
                errs.append(f"{candidate}: non-media {ctype}")
                continue
            m = re.search(r"/web/(\d+)", final)
            return {
                "verification": "verified",
                "resolved_url": final,
                "content_type": ctype,
                "bytes": str(len(raw)),
                "sha256": hashlib.sha256(raw).hexdigest(),
                "archive_timestamp": m.group(1) if m else closest_ts,
                "error": "",
            }
        except Exception as e:
            errs.append(f"{candidate}: {type(e).__name__}: {e}")
    return {
        "verification": "unverified",
        "resolved_url": "",
        "content_type": "",
        "bytes": "",
        "sha256": "",
        "archive_timestamp": "",
        "error": " | ".join(errs)[:3000],
    }


def main():
    pages = []
    asset_rows = []

    with ThreadPoolExecutor(max_workers=8) as pool:
        futures = {
            pool.submit(fetch_page, page, eid, original): (page, eid, original)
            for page, (eid, original) in EDITIONS.items()
        }
        for fut in as_completed(futures):
            row, assets = fut.result()
            pages.append(row)
            for u, how in assets:
                asset_rows.append(
                    {
                        "page": row["page"],
                        "edition_id": row["edition_id"],
                        "discovery": how,
                        "source_page": row["snapshot_url"] or row["original_url"],
                        "asset_url": u,
                    }
                )

    page, eid, seed = ROOT_SEED
    asset_rows.append(
        {
            "page": page,
            "edition_id": eid,
            "discovery": "verified_root_snapshot_reference",
            "source_page": wayback_raw("http://szb.cnssiot.cn/"),
            "asset_url": seed,
        }
    )

    rank = {
        "Pagepdf": 4,
        "Pagepic": 3,
        "verified_root_snapshot_reference": 2,
        "Img_reference": 1,
    }
    uniq = {}
    for row in asset_rows:
        key = (row["page"], row["edition_id"], row["asset_url"])
        old = uniq.get(key)
        if old is None or rank[row["discovery"]] > rank[old["discovery"]]:
            uniq[key] = row
    asset_rows = list(uniq.values())

    verified = []
    with ThreadPoolExecutor(max_workers=10) as pool:
        futures = {pool.submit(verify_media, row["asset_url"]): row for row in asset_rows}
        for fut in as_completed(futures):
            row = dict(futures[fut])
            row.update(fut.result())
            verified.append(row)

    pages.sort(key=lambda r: r["page"])
    verified.sort(
        key=lambda r: (
            r["page"],
            r["verification"] != "verified",
            r["discovery"] != "Pagepdf",
            r["asset_url"],
        )
    )

    with (OUT / "pages.csv").open("w", newline="", encoding="utf-8") as f:
        fields = [
            "page",
            "edition_id",
            "original_url",
            "snapshot_url",
            "status",
            "html_bytes",
            "asset_count",
            "error",
        ]
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerows(pages)

    with (OUT / "assets.csv").open("w", newline="", encoding="utf-8") as f:
        fields = [
            "page",
            "edition_id",
            "discovery",
            "source_page",
            "asset_url",
            "verification",
            "resolved_url",
            "content_type",
            "bytes",
            "sha256",
            "archive_timestamp",
            "error",
        ]
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerows(verified)

    report = {
        "edition_pages_expected": 8,
        "edition_pages_recovered": sum(r["status"] == "recovered" for r in pages),
        "asset_urls": len(verified),
        "verified_assets": sum(r["verification"] == "verified" for r in verified),
        "verified_pagepdf_assets": sum(
            r["verification"] == "verified" and r["discovery"] == "Pagepdf" for r in verified
        ),
        "verified_pages": sorted(
            {
                r["page"]
                for r in verified
                if r["verification"] == "verified"
                and r["discovery"] in {"Pagepdf", "Pagepic", "verified_root_snapshot_reference"}
            }
        ),
        "complete_a1_to_a8": all(
            any(
                r["page"] == page
                and r["verification"] == "verified"
                and r["discovery"] in {"Pagepdf", "Pagepic"}
                for r in verified
            )
            for page in EDITIONS
        ),
        "notes": [
            "Only Wayback responses are used for verification in this focused probe.",
            "A Pagepdf may be either high-resolution JPG or PDF in 53BK deployments.",
            "No newspaper binary is committed by this probe.",
        ],
    }
    (OUT / "report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(report, ensure_ascii=False, indent=2), flush=True)


if __name__ == "__main__":
    main()
