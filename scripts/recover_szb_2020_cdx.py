#!/usr/bin/env python3
"""One-request Wayback CDX recovery for 2020 szb.cnssiot.cn content routes."""
from __future__ import annotations

import csv
import json
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "data" / "szb_2020_cdx"
OUT.mkdir(parents=True, exist_ok=True)
UA = "qilu-shaonian-szb-2020-cdx/1.0 (+https://github.com/Evolution404/qilu-shaonian-electronic-archive)"


def main():
    params = {
        "url": "szb.cnssiot.cn/content/2020*",
        "output": "json",
        "filter": "statuscode:200",
        "collapse": "urlkey",
        "fl": "timestamp,original,statuscode,mimetype,digest",
        "limit": "5000",
    }
    url = "https://web.archive.org/cdx/search/cdx?" + urllib.parse.urlencode(params)
    rows = []
    error = ""
    try:
        req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/json"})
        with urllib.request.urlopen(req, timeout=35) as r:
            raw = r.read(8 * 1024 * 1024)
        data = json.loads(raw.decode("utf-8", "replace"))
        if data:
            header = data[0]
            for values in data[1:]:
                row = dict(zip(header, values))
                original = row.get("original", "")
                row["archive_url"] = f"https://web.archive.org/web/{row.get('timestamp','')}id_/{original}"
                rows.append(row)
    except Exception as e:
        error = f"{type(e).__name__}: {e}"

    fields = ["timestamp", "original", "statuscode", "mimetype", "digest", "archive_url"]
    with (OUT / "rows.csv").open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields); w.writeheader(); w.writerows(rows)

    dates = sorted({
        part
        for r in rows
        for part in [r.get("original", "").split("/content/", 1)[-1][:10]]
        if len(part) == 10 and part[4] == "-" and part[7] == "-"
    })
    report = {
        "query": params["url"],
        "cdx_rows": len(rows),
        "publication_date_candidates": dates,
        "edition_route_rows": sum("edition" in r.get("original", "").lower() for r in rows),
        "article_route_rows": sum("edition" not in r.get("original", "").lower() and "/content/" in r.get("original", "").lower() for r in rows),
        "query_error": error,
        "valid_negative_result": (not error),
        "notes": [
            "A zero row count is treated as a negative result only if query_error is empty.",
            "No archived page bytes are committed by this inventory step."
        ]
    }
    (OUT / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2), flush=True)


if __name__ == "__main__":
    main()
