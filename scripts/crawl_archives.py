#!/usr/bin/env python3
"""Enumerate archival URL indexes for Qilu Shaonian electronic-edition systems.

This crawler intentionally stores metadata/URLs only. It does not mirror article bodies.
Sources queried:
- Internet Archive Wayback CDX
- Common Crawl URL indexes
"""
from __future__ import annotations

import csv
import datetime as dt
import json
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "data" / "archive_crawl"
OUT.mkdir(parents=True, exist_ok=True)

UA = "qilu-shaonian-electronic-archive/1.0 (+https://github.com/Evolution404/qilu-shaonian-electronic-archive)"
TIMEOUT = 45

WAYBACK_TARGETS = [
    "www.dzwww.com/qilushaonian/*",
    "dzwww.com/qilushaonian/*",
    "202.102.188.131/qilushaonian/*",
    "szb.cnssiot.cn/*",
    "*.cnssiot.cn/*",
    "*.sdview.com.cn/*",
    "*.dzdaily.com.cn/*",
]

COMMONCRAWL_TARGETS = [
    "www.dzwww.com/qilushaonian/*",
    "dzwww.com/qilushaonian/*",
    "202.102.188.131/qilushaonian/*",
    "szb.cnssiot.cn/*",
]

# Keep all direct qilu-shaonian paths plus later CMS URLs under the verified szb host.
INTERESTING = re.compile(
    r"(?:qilushaonian|qilu[-_]?shaonian|qlsn|szb\.cnssiot\.cn)", re.I
)
EXCLUDE = re.compile(r"(?:^|//)paper\.cnssiot\.cn(?:/|$)", re.I)


def get(url: str, *, accept: str = "application/json,text/plain,*/*") -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": accept})
    with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
        return r.read()


def safe_get(url: str) -> tuple[bytes | None, str | None]:
    try:
        return get(url), None
    except Exception as e:  # network/index service errors are recorded, not fatal
        return None, f"{type(e).__name__}: {e}"


def normalize_original(url: str) -> str:
    return url.replace(":80/", "/").replace(":443/", "/")


def interesting(url: str) -> bool:
    return bool(INTERESTING.search(url)) and not EXCLUDE.search(url)


def crawl_wayback() -> tuple[list[dict[str, str]], list[dict[str, str]]]:
    rows: dict[tuple[str, str, str], dict[str, str]] = {}
    errors: list[dict[str, str]] = []
    for target in WAYBACK_TARGETS:
        params = {
            "url": target,
            "output": "json",
            "fl": "timestamp,original,statuscode,mimetype,digest",
            "filter": "statuscode:200",
            "collapse": "urlkey",
        }
        url = "https://web.archive.org/cdx/search/cdx?" + urllib.parse.urlencode(params)
        raw, err = safe_get(url)
        if err:
            errors.append({"source": "wayback", "target": target, "error": err})
            continue
        try:
            payload = json.loads(raw.decode("utf-8", "replace"))
        except Exception as e:
            errors.append({"source": "wayback", "target": target, "error": f"decode: {e}"})
            continue
        if not payload:
            continue
        header = payload[0]
        for item in payload[1:]:
            d = dict(zip(header, item))
            original = normalize_original(d.get("original", ""))
            if not interesting(original):
                continue
            timestamp = d.get("timestamp", "")
            key = (original, timestamp, d.get("digest", ""))
            rows[key] = {
                "timestamp": timestamp,
                "original": original,
                "statuscode": d.get("statuscode", ""),
                "mimetype": d.get("mimetype", ""),
                "digest": d.get("digest", ""),
                "archive_url": f"https://web.archive.org/web/{timestamp}id_/{original}" if timestamp else "",
                "query_target": target,
            }
        time.sleep(0.25)
    return sorted(rows.values(), key=lambda x: (x["original"], x["timestamp"])), errors


def get_commoncrawl_indexes() -> list[dict]:
    raw = get("https://index.commoncrawl.org/collinfo.json")
    data = json.loads(raw.decode("utf-8", "replace"))
    return data if isinstance(data, list) else []


def crawl_commoncrawl() -> tuple[list[dict[str, str]], list[dict[str, str]]]:
    rows: dict[tuple[str, str, str], dict[str, str]] = {}
    errors: list[dict[str, str]] = []
    try:
        indexes = get_commoncrawl_indexes()
    except Exception as e:
        return [], [{"source": "commoncrawl", "target": "collinfo", "error": f"{type(e).__name__}: {e}"}]

    # All available indexes are queried because old electronic-newspaper URLs are sparse.
    for index in indexes:
        api = index.get("cdx-api")
        index_id = index.get("id", "")
        if not api:
            continue
        for target in COMMONCRAWL_TARGETS:
            q = api + "?" + urllib.parse.urlencode({"url": target, "output": "json", "filter": "status:200"})
            raw, err = safe_get(q)
            if err:
                errors.append({"source": "commoncrawl", "target": f"{index_id}:{target}", "error": err})
                continue
            text = raw.decode("utf-8", "replace").strip()
            if not text:
                continue
            for line in text.splitlines():
                try:
                    d = json.loads(line)
                except json.JSONDecodeError:
                    continue
                original = normalize_original(d.get("url", ""))
                if not interesting(original):
                    continue
                ts = d.get("timestamp", "")
                digest = d.get("digest", "")
                key = (original, ts, digest)
                rows[key] = {
                    "timestamp": ts,
                    "original": original,
                    "status": str(d.get("status", "")),
                    "mime": d.get("mime", d.get("mime-detected", "")),
                    "digest": digest,
                    "commoncrawl_index": index_id,
                    "filename": d.get("filename", ""),
                    "offset": str(d.get("offset", "")),
                    "length": str(d.get("length", "")),
                    "query_target": target,
                }
            time.sleep(0.12)
    return sorted(rows.values(), key=lambda x: (x["original"], x["timestamp"])), errors


def write_csv(path: Path, rows: list[dict[str, str]], fields: list[str]) -> None:
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)


def summarize_by_host(rows: list[dict[str, str]]) -> dict[str, int]:
    out: dict[str, int] = defaultdict(int)
    for r in rows:
        host = urllib.parse.urlparse(r.get("original", "")).hostname or ""
        out[host] += 1
    return dict(sorted(out.items()))


def main() -> int:
    wb_rows, wb_errors = crawl_wayback()
    cc_rows, cc_errors = crawl_commoncrawl()

    write_csv(
        OUT / "wayback_urls.csv",
        wb_rows,
        ["timestamp", "original", "statuscode", "mimetype", "digest", "archive_url", "query_target"],
    )
    write_csv(
        OUT / "commoncrawl_urls.csv",
        cc_rows,
        ["timestamp", "original", "status", "mime", "digest", "commoncrawl_index", "filename", "offset", "length", "query_target"],
    )

    report = {
        "generated_at_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
        "wayback_unique_records": len(wb_rows),
        "commoncrawl_unique_records": len(cc_rows),
        "wayback_by_host": summarize_by_host(wb_rows),
        "commoncrawl_by_host": summarize_by_host(cc_rows),
        "errors": wb_errors + cc_errors,
        "notes": [
            "paper.cnssiot.cn is deliberately excluded because it was verified as 山东青年报, not 齐鲁少年.",
            "Capture timestamps are archive timestamps and must not be treated as newspaper publication dates.",
        ],
    }
    (OUT / "crawl_report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
