#!/usr/bin/env python3
"""Recover historical versions of YunZhan's fixed Qilu Shaonian publication image URL.

The publication directory uses a stable image URL for 《齐鲁少年》. Wayback may therefore
contain different historical page images under the same URL as the site's latest image changed.
This script inventories all archived 200 responses, groups them by digest, and transiently
verifies image dimensions/hashes. It commits metadata only, never third-party image bytes.
"""
from __future__ import annotations

import csv
import hashlib
import io
import json
import urllib.parse
import urllib.request
from collections import defaultdict
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "data" / "yunzhan_cover_history"
OUT.mkdir(parents=True, exist_ok=True)
UA = "qilu-shaonian-yunzhan-history/1.0 (+https://github.com/Evolution404/qilu-shaonian-electronic-archive)"
TARGET = "https://www.yunzhan365.com/newspapers/publications/images/齐鲁少年 .jpg"
MAX_IMAGE = 20 * 1024 * 1024


def get(url: str, limit: int, timeout: int = 30):
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "*/*"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        raw = r.read(limit + 1)
        if len(raw) > limit:
            raise ValueError("response too large")
        return raw, r.geturl(), {k.lower(): v for k, v in r.headers.items()}


def cdx_rows(target: str):
    params = {
        "url": target,
        "output": "json",
        "filter": "statuscode:200",
        "fl": "timestamp,original,statuscode,mimetype,digest,length",
        "limit": "2000",
    }
    url = "https://web.archive.org/cdx/search/cdx?" + urllib.parse.urlencode(params)
    raw, _, _ = get(url, 8 * 1024 * 1024, 40)
    data = json.loads(raw.decode("utf-8", "replace"))
    if not data:
        return []
    header = data[0]
    return [dict(zip(header, values)) for values in data[1:]]


def verify(snapshot_url: str):
    try:
        raw, final, headers = get(snapshot_url, MAX_IMAGE, 25)
        width = height = fmt = ""
        try:
            im = Image.open(io.BytesIO(raw))
            width, height, fmt = str(im.width), str(im.height), im.format or ""
        except Exception:
            pass
        return {
            "resolved_url": final,
            "content_type": headers.get("content-type", ""),
            "bytes": str(len(raw)),
            "sha256": hashlib.sha256(raw).hexdigest(),
            "width": width,
            "height": height,
            "image_format": fmt,
            "error": "",
        }
    except Exception as e:
        return {
            "resolved_url": "", "content_type": "", "bytes": "", "sha256": "",
            "width": "", "height": "", "image_format": "", "error": f"{type(e).__name__}: {e}"[:1000]
        }


def main():
    rows = []
    error = ""
    targets = [TARGET, TARGET.replace(" ", "%20")]
    seen = set()
    try:
        for target in targets:
            for row in cdx_rows(target):
                key = (row.get("timestamp", ""), row.get("original", ""), row.get("digest", ""))
                if key in seen:
                    continue
                seen.add(key)
                rows.append(row)
    except Exception as e:
        error = f"{type(e).__name__}: {e}"

    by_digest = defaultdict(list)
    for row in rows:
        by_digest[row.get("digest", "")].append(row)

    verified = []
    for digest, group in sorted(by_digest.items(), key=lambda kv: min(r.get("timestamp", "") for r in kv[1])):
        representative = sorted(group, key=lambda r: r.get("timestamp", ""))[0]
        ts = representative.get("timestamp", "")
        original = representative.get("original", TARGET)
        snapshot = f"https://web.archive.org/web/{ts}id_/{original}"
        meta = verify(snapshot)
        verified.append({
            "cdx_digest": digest,
            "first_timestamp": min(r.get("timestamp", "") for r in group),
            "last_timestamp": max(r.get("timestamp", "") for r in group),
            "snapshot_count": str(len(group)),
            "original_url": original,
            "archive_url": snapshot,
            **meta,
        })

    fields = [
        "cdx_digest", "first_timestamp", "last_timestamp", "snapshot_count", "original_url",
        "archive_url", "resolved_url", "content_type", "bytes", "sha256", "width", "height",
        "image_format", "error"
    ]
    with (OUT / "versions.csv").open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields); w.writeheader(); w.writerows(verified)

    report = {
        "target_url": TARGET,
        "cdx_rows": len(rows),
        "unique_cdx_digests": len(by_digest),
        "verified_image_versions": sum(bool(r["width"]) for r in verified),
        "distinct_verified_sha256": len({r["sha256"] for r in verified if r["sha256"]}),
        "portrait_fullpage_geometry_versions": sum(
            bool(r["width"] and r["height"]) and int(r["height"]) > int(r["width"]) * 1.25 and int(r["height"]) >= 1000
            for r in verified
        ),
        "query_error": error,
        "versions": [
            {k: r[k] for k in ("first_timestamp", "last_timestamp", "snapshot_count", "sha256", "width", "height", "archive_url", "error")}
            for r in verified
        ],
        "notes": [
            "Different digests under the fixed publication-image URL may represent different historical editions, but require visual/OCR verification before promotion.",
            "Only metadata is committed; archived image bytes are transient."
        ]
    }
    (OUT / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2), flush=True)


if __name__ == "__main__":
    main()
