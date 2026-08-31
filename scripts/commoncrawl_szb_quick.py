#!/usr/bin/env python3
"""Fast Common Crawl profile for the verified 2021-12-25 SZB issue.

The full WARC recovery engine is retained in commoncrawl_szb_targeted.py. This wrapper
reduces the expensive discovery matrix to 2022 crawls and only deterministic URLs:
root, A1-A8 edition routes, and the one A1 media URL leaked by the verified root snapshot.
Index requests use a single short attempt; any hit still goes through the full WARC-body
verification and second-pass media extraction implemented by the main engine.
"""
from __future__ import annotations

import json
import re

import commoncrawl_szb_targeted as cc

ORIGINAL_HTTP_GET = cc.http_get


def quick_http_get(url: str, *, headers=None, max_bytes=None, retries=1):
    # Index endpoints occasionally hang. One short attempt gives a deterministic CI result
    # and is preferable to spending the entire workflow budget retrying a negative lookup.
    return ORIGINAL_HTTP_GET(url, headers=headers, max_bytes=max_bytes, retries=1)


def quick_indexes():
    raw, _, _ = quick_http_get(
        "https://index.commoncrawl.org/collinfo.json",
        max_bytes=4 * 1024 * 1024,
    )
    data = json.loads(raw.decode("utf-8", "replace"))
    out = []
    for item in data:
        m = re.search(r"CC-MAIN-(20\d{2})-(\d+)", item.get("id", ""))
        if not m or not item.get("cdx-api"):
            continue
        if int(m.group(1)) == 2022:
            out.append(item)
    out.sort(key=lambda x: x.get("id", ""))
    return out


def quick_targets():
    rows = [
        {
            "page": "",
            "edition_id": "",
            "provenance": "known_root",
            "url": "http://szb.cnssiot.cn/",
        }
    ]
    for page, (eid, url) in cc.EDITIONS.items():
        rows.append(
            {
                "page": page,
                "edition_id": eid,
                "provenance": "known_edition_route",
                "url": url,
            }
        )
    rows.extend(
        [
            {
                "page": "A1",
                "edition_id": "326",
                "provenance": "known_root_media_reference",
                "url": cc.A1_MEDIA,
            },
            {
                "page": "A1",
                "edition_id": "326",
                "provenance": "known_root_media_reference_no_query",
                "url": cc.A1_MEDIA.split("?", 1)[0],
            },
        ]
    )
    return rows


cc.TIMEOUT = 12
cc.INDEX_WORKERS = 12
cc.WARC_WORKERS = 6
cc.http_get = quick_http_get
cc.selected_indexes = quick_indexes
cc.target_rows = quick_targets

if __name__ == "__main__":
    raise SystemExit(cc.main())
