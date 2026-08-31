#!/usr/bin/env python3
"""Quarterly Wayback sampling for YunZhan's fixed 《齐鲁少年》 publication image URL.

This is a low-cost fallback for CDX timeouts. It asks archive.org/wayback/available at quarterly
anchors, resolves unique closest snapshots, and transiently hashes/inspects any archived images.
No image bytes are committed.
"""
from __future__ import annotations

import csv
import hashlib
import io
import json
import re
import time
import urllib.parse
import urllib.request
from pathlib import Path
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "data" / "yunzhan_cover_quarterly"
OUT.mkdir(parents=True, exist_ok=True)
TARGET = "https://www.yunzhan365.com/newspapers/publications/images/齐鲁少年 .jpg"
UA = "qilu-shaonian-yunzhan-quarterly/1.0 (+https://github.com/Evolution404/qilu-shaonian-electronic-archive)"


def get(url: str, limit: int, timeout: int = 20):
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "*/*"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        raw = r.read(limit + 1)
        if len(raw) > limit:
            raise ValueError("response too large")
        return raw, r.geturl(), {k.lower(): v for k, v in r.headers.items()}


def available(date: str):
    api = "https://archive.org/wayback/available?" + urllib.parse.urlencode({"url": TARGET, "timestamp": date})
    raw, _, _ = get(api, 1024 * 1024, 18)
    data = json.loads(raw.decode("utf-8", "replace"))
    c = (data.get("archived_snapshots") or {}).get("closest") or {}
    if not c.get("available") or not c.get("url"):
        return "", ""
    url = re.sub(r"/web/(\d+)/", r"/web/\1id_/", c["url"], count=1)
    return url, c.get("timestamp", "")


def inspect(url: str):
    try:
        raw, final, headers = get(url, 20 * 1024 * 1024, 25)
        width = height = fmt = ""
        try:
            im = Image.open(io.BytesIO(raw)); width = str(im.width); height = str(im.height); fmt = im.format or ""
        except Exception:
            pass
        return final, headers.get("content-type", ""), str(len(raw)), hashlib.sha256(raw).hexdigest(), width, height, fmt, ""
    except Exception as e:
        return "", "", "", "", "", "", "", f"{type(e).__name__}: {e}"[:1000]


def main():
    anchors = [f"{year}{month:02d}15" for year in range(2021, 2027) for month in (1, 4, 7, 10)]
    rows = []
    snapshots = {}
    for date in anchors:
        snap = ts = error = ""
        try:
            snap, ts = available(date)
        except Exception as e:
            error = f"{type(e).__name__}: {e}"[:500]
        rows.append({"anchor_date": date, "snapshot_timestamp": ts, "archive_url": snap, "availability_error": error})
        if snap:
            snapshots.setdefault((ts, snap), None)
        time.sleep(0.25)

    versions = []
    for ts, snap in sorted(snapshots):
        final, ctype, size, sha, width, height, fmt, error = inspect(snap)
        versions.append({
            "snapshot_timestamp": ts, "archive_url": snap, "resolved_url": final, "content_type": ctype,
            "bytes": size, "sha256": sha, "width": width, "height": height, "image_format": fmt, "error": error,
        })

    with (OUT / "anchors.csv").open("w", newline="", encoding="utf-8") as f:
        fields = ["anchor_date", "snapshot_timestamp", "archive_url", "availability_error"]
        w = csv.DictWriter(f, fieldnames=fields); w.writeheader(); w.writerows(rows)
    with (OUT / "versions.csv").open("w", newline="", encoding="utf-8") as f:
        fields = ["snapshot_timestamp", "archive_url", "resolved_url", "content_type", "bytes", "sha256", "width", "height", "image_format", "error"]
        w = csv.DictWriter(f, fieldnames=fields); w.writeheader(); w.writerows(versions)

    report = {
        "target_url": TARGET,
        "quarterly_anchors": len(anchors),
        "anchors_with_snapshot": sum(bool(r["archive_url"]) for r in rows),
        "availability_errors": sum(bool(r["availability_error"]) for r in rows),
        "unique_closest_snapshots": len(versions),
        "verified_image_snapshots": sum(bool(v["width"]) for v in versions),
        "distinct_verified_sha256": len({v["sha256"] for v in versions if v["sha256"]}),
        "versions": versions,
        "notes": [
            "Quarterly available sampling does not prove completeness; it is a fallback for the timed-out exact CDX history query.",
            "Different verified SHA-256 values would indicate historical content changes under the fixed image URL.",
            "No archived image bytes are committed."
        ]
    }
    (OUT / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2), flush=True)


if __name__ == "__main__":
    main()
