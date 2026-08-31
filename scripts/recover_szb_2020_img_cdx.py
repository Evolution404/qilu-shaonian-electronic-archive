#!/usr/bin/env python3
"""Focused Wayback CDX inventory for 2020 szb.cnssiot.cn Img assets."""
from __future__ import annotations

import csv
import json
import re
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "data" / "szb_2020_img_cdx"
OUT.mkdir(parents=True, exist_ok=True)
UA = "qilu-shaonian-szb-2020-img-cdx/1.0 (+https://github.com/Evolution404/qilu-shaonian-electronic-archive)"


def main():
    params = {
        "url": "szb.cnssiot.cn/Img/2020*",
        "output": "json",
        "filter": "statuscode:200",
        "collapse": "urlkey",
        "fl": "timestamp,original,statuscode,mimetype,digest,length",
        "limit": "10000",
    }
    url = "https://web.archive.org/cdx/search/cdx?" + urllib.parse.urlencode(params)
    rows = []
    error = ""
    try:
        req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/json"})
        with urllib.request.urlopen(req, timeout=40) as r:
            raw = r.read(12 * 1024 * 1024)
        data = json.loads(raw.decode("utf-8", "replace"))
        if data:
            header = data[0]
            for values in data[1:]:
                row = dict(zip(header, values))
                row["archive_url"] = f"https://web.archive.org/web/{row.get('timestamp','')}id_/{row.get('original','')}"
                rows.append(row)
    except Exception as e:
        error = f"{type(e).__name__}: {e}"

    fields = ["timestamp", "original", "statuscode", "mimetype", "digest", "length", "archive_url"]
    with (OUT / "rows.csv").open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields); w.writeheader(); w.writerows(rows)

    page_like = []
    for r in rows:
        u = r.get("original", "")
        name = urllib.parse.urlsplit(u).path.rsplit("/", 1)[-1]
        if re.search(r"(?i)(?:mobile|pc)20\d{6}[0-9a-f]{32}\.jpg$", name) or name.lower().endswith(".pdf"):
            page_like.append(u)
    report = {
        "query": params["url"],
        "cdx_rows": len(rows),
        "image_rows": sum(str(r.get("mimetype", "")).startswith("image/") for r in rows),
        "pdf_rows": sum("pdf" in str(r.get("mimetype", "")).lower() or str(r.get("original", "")).lower().endswith(".pdf") for r in rows),
        "page_asset_pattern_rows": len(page_like),
        "page_asset_examples": page_like[:50],
        "query_error": error,
        "valid_negative_result": (not error),
        "notes": [
            "This inventory targets archived media independently of missing 2020 content pages.",
            "A matching filename is only a candidate until the media itself and newspaper identity are verified."
        ]
    }
    (OUT / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2), flush=True)


if __name__ == "__main__":
    main()
