#!/usr/bin/env python3
"""Parallel archival URL discovery for post-2000 Qilu Shaonian research.

This script stores URL/index metadata only. It deliberately does not mirror article bodies
or newspaper scans. Known Qilu Shaonian web identities are queried directly so that old
pages whose URLs do not contain the newspaper name (Sina/Qzone) can still be discovered.
"""
from __future__ import annotations

import csv
import datetime as dt
import json
import re
import time
import urllib.parse
import urllib.request
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "data" / "archive_crawl"
OUT.mkdir(parents=True, exist_ok=True)
UA = "qilu-shaonian-electronic-archive/2.0 (+https://github.com/Evolution404/qilu-shaonian-electronic-archive)"
TIMEOUT = 35

WAYBACK_TARGETS = [
    "www.dzwww.com/qilushaonian/*",
    "dzwww.com/qilushaonian/*",
    "202.102.188.131/qilushaonian/*",
    "szb.cnssiot.cn/*",
    "qlsn.com/*",
    "www.qlsn.com/*",
    "857087447.qzone.qq.com/*",
    "blog.sina.com.cn/s/blog_4c4fc7d9*",
    "blog.sina.cn/dpool/blog/s/blog_4c4fc7d9*",
    "qlsnreadship.wordpress.com/*",
    "qlsnreadship.files.wordpress.com/*",
    "www.yunzhan365.com/newspapers/publications/qilushaonian*",
]

COMMONCRAWL_TARGETS = [
    "www.dzwww.com/qilushaonian/*",
    "dzwww.com/qilushaonian/*",
    "202.102.188.131/qilushaonian/*",
    "szb.cnssiot.cn/*",
    "qlsn.com/*",
    "www.qlsn.com/*",
    "857087447.qzone.qq.com/*",
    "blog.sina.com.cn/s/blog_4c4fc7d9*",
    "qlsnreadship.wordpress.com/*",
    "qlsnreadship.files.wordpress.com/*",
    "www.yunzhan365.com/newspapers/publications/qilushaonian*",
]

EXCLUDE = re.compile(r"(?:^|//)paper\.cnssiot\.cn(?:/|$)", re.I)
SINA_ID = re.compile(r"blog_4c4fc7d9", re.I)
ALLOWED_HOSTS = {
    "www.dzwww.com", "dzwww.com", "202.102.188.131", "szb.cnssiot.cn",
    "qlsn.com", "www.qlsn.com", "857087447.qzone.qq.com",
    "blog.sina.com.cn", "blog.sina.cn", "qlsnreadship.wordpress.com",
    "qlsnreadship.files.wordpress.com", "www.yunzhan365.com",
}


def get(url: str) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/json,text/plain,*/*"})
    with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
        return r.read()


def normalize(url: str) -> str:
    return url.replace(":80/", "/").replace(":443/", "/")


def accept(url: str) -> bool:
    if not url or EXCLUDE.search(url):
        return False
    parsed = urllib.parse.urlparse(url if "://" in url else "http://" + url)
    host = (parsed.hostname or "").lower()
    if host not in ALLOWED_HOSTS:
        return False
    if host in {"blog.sina.com.cn", "blog.sina.cn"}:
        return bool(SINA_ID.search(url))
    return True


def wayback_one(target: str) -> tuple[list[dict[str, str]], dict[str, str] | None]:
    params = {
        "url": target,
        "output": "json",
        "fl": "timestamp,original,statuscode,mimetype,digest",
        "filter": "statuscode:200",
        "collapse": "urlkey",
    }
    endpoint = "https://web.archive.org/cdx/search/cdx?" + urllib.parse.urlencode(params)
    try:
        payload = json.loads(get(endpoint).decode("utf-8", "replace"))
        if not payload:
            return [], None
        header = payload[0]
        rows = []
        for item in payload[1:]:
            d = dict(zip(header, item))
            original = normalize(d.get("original", ""))
            if not accept(original):
                continue
            ts = d.get("timestamp", "")
            rows.append({
                "timestamp": ts,
                "original": original,
                "statuscode": d.get("statuscode", ""),
                "mimetype": d.get("mimetype", ""),
                "digest": d.get("digest", ""),
                "archive_url": f"https://web.archive.org/web/{ts}id_/{original}" if ts else "",
                "query_target": target,
            })
        return rows, None
    except Exception as exc:
        return [], {"source": "wayback", "target": target, "error": f"{type(exc).__name__}: {exc}"}


def crawl_wayback() -> tuple[list[dict[str, str]], list[dict[str, str]]]:
    found: dict[tuple[str, str, str], dict[str, str]] = {}
    errors: list[dict[str, str]] = []
    with ThreadPoolExecutor(max_workers=6) as pool:
        futures = {pool.submit(wayback_one, t): t for t in WAYBACK_TARGETS}
        for future in as_completed(futures):
            rows, err = future.result()
            if err:
                errors.append(err)
            for row in rows:
                found[(row["original"], row["timestamp"], row["digest"])] = row
    return sorted(found.values(), key=lambda r: (r["original"], r["timestamp"])), errors


def commoncrawl_indexes() -> list[dict]:
    data = json.loads(get("https://index.commoncrawl.org/collinfo.json").decode("utf-8", "replace"))
    if not isinstance(data, list):
        return []
    # Common Crawl's public web indexes begin in 2008; every available index is relevant
    # to the requested 2000+ archive, but ordering newest-first is not assumed.
    return [x for x in data if x.get("cdx-api")]


def cc_one(index: dict, target: str) -> tuple[list[dict[str, str]], dict[str, str] | None]:
    api = index.get("cdx-api", "")
    index_id = index.get("id", "")
    endpoint = api + "?" + urllib.parse.urlencode({"url": target, "output": "json", "filter": "status:200"})
    try:
        text = get(endpoint).decode("utf-8", "replace").strip()
        if not text:
            return [], None
        rows = []
        for line in text.splitlines():
            try:
                d = json.loads(line)
            except json.JSONDecodeError:
                continue
            original = normalize(d.get("url", ""))
            if not accept(original):
                continue
            rows.append({
                "timestamp": d.get("timestamp", ""),
                "original": original,
                "status": str(d.get("status", "")),
                "mime": d.get("mime", d.get("mime-detected", "")),
                "digest": d.get("digest", ""),
                "commoncrawl_index": index_id,
                "filename": d.get("filename", ""),
                "offset": str(d.get("offset", "")),
                "length": str(d.get("length", "")),
                "query_target": target,
            })
        return rows, None
    except Exception as exc:
        return [], {"source": "commoncrawl", "target": f"{index_id}:{target}", "error": f"{type(exc).__name__}: {exc}"}


def crawl_commoncrawl() -> tuple[list[dict[str, str]], list[dict[str, str]]]:
    try:
        indexes = commoncrawl_indexes()
    except Exception as exc:
        return [], [{"source": "commoncrawl", "target": "collinfo", "error": f"{type(exc).__name__}: {exc}"}]
    found: dict[tuple[str, str, str], dict[str, str]] = {}
    errors: list[dict[str, str]] = []
    tasks = [(idx, target) for idx in indexes for target in COMMONCRAWL_TARGETS]
    # Bounded concurrency cuts a multi-hour serial scan to minutes without hammering the index service.
    with ThreadPoolExecutor(max_workers=10) as pool:
        futures = {pool.submit(cc_one, idx, target): (idx.get("id", ""), target) for idx, target in tasks}
        for future in as_completed(futures):
            rows, err = future.result()
            if err:
                errors.append(err)
            for row in rows:
                found[(row["original"], row["timestamp"], row["digest"])] = row
    return sorted(found.values(), key=lambda r: (r["original"], r["timestamp"])), errors


def write_csv(path: Path, rows: list[dict[str, str]], fields: list[str]) -> None:
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)


def hosts(rows: list[dict[str, str]]) -> dict[str, int]:
    c = Counter()
    for r in rows:
        c[urllib.parse.urlparse(r.get("original", "")).hostname or ""] += 1
    return dict(sorted(c.items()))


def main() -> int:
    started = time.monotonic()
    wb_rows, wb_errors = crawl_wayback()
    cc_rows, cc_errors = crawl_commoncrawl()
    write_csv(OUT / "wayback_urls.csv", wb_rows,
              ["timestamp", "original", "statuscode", "mimetype", "digest", "archive_url", "query_target"])
    write_csv(OUT / "commoncrawl_urls.csv", cc_rows,
              ["timestamp", "original", "status", "mime", "digest", "commoncrawl_index", "filename", "offset", "length", "query_target"])
    report = {
        "generated_at_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
        "elapsed_seconds": round(time.monotonic() - started, 2),
        "wayback_unique_records": len(wb_rows),
        "commoncrawl_unique_records": len(cc_rows),
        "wayback_by_host": hosts(wb_rows),
        "commoncrawl_by_host": hosts(cc_rows),
        "errors": wb_errors + cc_errors,
        "targets": {"wayback": WAYBACK_TARGETS, "commoncrawl": COMMONCRAWL_TARGETS},
        "notes": [
            "Capture timestamps are archive timestamps, not newspaper publication dates.",
            "Sina/Qzone records are discovery leads until page content proves they are electronic-edition material.",
            "paper.cnssiot.cn remains excluded because it was verified as 山东青年报 rather than 齐鲁少年.",
        ],
    }
    (OUT / "crawl_report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
