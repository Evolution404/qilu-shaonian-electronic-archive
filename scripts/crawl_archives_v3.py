#!/usr/bin/env python3
"""Targeted archive discovery for post-2000 《齐鲁少年》 electronic editions.

Wayback is queried broadly for known/candidate identities. Common Crawl is queried only
for year windows in which each identity could realistically have been active. The output
contains URL/index metadata only; candidates are not promoted to verified editions here.
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
UA = "qilu-shaonian-electronic-archive/3.1 (+https://github.com/Evolution404/qilu-shaonian-electronic-archive)"
TIMEOUT = 20
WAYBACK_WORKERS = 10
CC_WORKERS = 20

WAYBACK_TARGETS = [
    "www.dzwww.com/qilushaonian/*",
    "dzwww.com/qilushaonian/*",
    "202.102.188.131/qilushaonian/*",
    "szb.cnssiot.cn/*",
    "qlsn.com/*", "www.qlsn.com/*",
    "qilushaonian.com/*", "www.qilushaonian.com/*",
    "qlshaonian.com/*", "www.qlshaonian.com/*",
    "857087447.qzone.qq.com/*",
    "blog.sina.com.cn/s/blog_4c4fc7d9*",
    "blog.sina.cn/dpool/blog/s/blog_4c4fc7d9*",
    "qlsnreadship.wordpress.com/*",
    "qlsnreadship.files.wordpress.com/*",
    "www.yunzhan365.com/newspapers/publications/qilushaonian*",
]

# Common Crawl public indexes begin in 2008. Restrict each target to a realistic
# activity window; this preserves relevant captures while avoiding thousands of
# guaranteed-empty index requests.
CC_TARGET_WINDOWS = {
    "qlsn.com/*": (2008, 2017),
    "www.qlsn.com/*": (2008, 2017),
    "qilushaonian.com/*": (2008, 2017),
    "www.qilushaonian.com/*": (2008, 2017),
    "qlshaonian.com/*": (2008, 2017),
    "www.qlshaonian.com/*": (2008, 2017),
    "857087447.qzone.qq.com/*": (2008, 2013),
    "blog.sina.com.cn/s/blog_4c4fc7d9*": (2008, 2014),
    "szb.cnssiot.cn/*": (2020, 2023),
    "qlsnreadship.wordpress.com/*": (2019, 2021),
    "qlsnreadship.files.wordpress.com/*": (2019, 2021),
    "www.yunzhan365.com/newspapers/publications/qilushaonian*": (2021, 2026),
}

# Confirmed false positives. qlsn.cn / www.qlsn.cn is 齐鲁三农网, not 《齐鲁少年》.
EXCLUDE = re.compile(r"(?:^|//)(?:paper\.cnssiot\.cn|(?:www\.)?qlsn\.cn)(?:/|$)", re.I)
SINA_ID = re.compile(r"blog_4c4fc7d9", re.I)
CC_YEAR = re.compile(r"CC-MAIN-(20\d{2})-")


def get(url: str) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/json,text/plain,*/*"})
    with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
        return r.read()


def normalize(url: str) -> str:
    return url.replace(":80/", "/").replace(":443/", "/")


def archive_url(ts: str, original: str) -> str:
    return f"https://web.archive.org/web/{ts}id_/{original}" if ts else ""


def wayback_one(target: str):
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
            if not original or EXCLUDE.search(original):
                continue
            if "blog.sina" in original and not SINA_ID.search(original):
                continue
            ts = d.get("timestamp", "")
            rows.append({
                "timestamp": ts,
                "original": original,
                "statuscode": d.get("statuscode", ""),
                "mimetype": d.get("mimetype", ""),
                "digest": d.get("digest", ""),
                "archive_url": archive_url(ts, original),
                "query_target": target,
            })
        return rows, None
    except Exception as exc:
        return [], {"source": "wayback", "target": target, "error": f"{type(exc).__name__}: {exc}"}


def crawl_wayback():
    found = {}
    errors = []
    with ThreadPoolExecutor(max_workers=WAYBACK_WORKERS) as pool:
        futures = {pool.submit(wayback_one, t): t for t in WAYBACK_TARGETS}
        for future in as_completed(futures):
            rows, err = future.result()
            if err:
                errors.append(err)
            for row in rows:
                found[(row["original"], row["timestamp"], row["digest"])] = row
            print(f"wayback done: {futures[future]} rows={len(rows)}", flush=True)
    return sorted(found.values(), key=lambda r: (r["original"], r["timestamp"])), errors


def cc_indexes():
    data = json.loads(get("https://index.commoncrawl.org/collinfo.json").decode("utf-8", "replace"))
    out = []
    for item in data if isinstance(data, list) else []:
        api = item.get("cdx-api")
        index_id = item.get("id", "")
        m = CC_YEAR.search(index_id)
        if not api or not m:
            continue
        item = dict(item)
        item["_year"] = int(m.group(1))
        out.append(item)
    return out


def cc_one(index: dict, target: str):
    endpoint = index["cdx-api"] + "?" + urllib.parse.urlencode({
        "url": target,
        "output": "json",
        "filter": "status:200",
    })
    try:
        text = get(endpoint).decode("utf-8", "replace").strip()
        rows = []
        for line in text.splitlines() if text else []:
            try:
                d = json.loads(line)
            except json.JSONDecodeError:
                continue
            original = normalize(d.get("url", ""))
            if not original or EXCLUDE.search(original):
                continue
            if "blog.sina" in original and not SINA_ID.search(original):
                continue
            rows.append({
                "timestamp": d.get("timestamp", ""),
                "original": original,
                "status": str(d.get("status", "")),
                "mime": d.get("mime", d.get("mime-detected", "")),
                "digest": d.get("digest", ""),
                "commoncrawl_index": index.get("id", ""),
                "filename": d.get("filename", ""),
                "offset": str(d.get("offset", "")),
                "length": str(d.get("length", "")),
                "query_target": target,
            })
        return rows, None
    except Exception as exc:
        return [], {"source": "commoncrawl", "target": f"{index.get('id','')}:{target}", "error": f"{type(exc).__name__}: {exc}"}


def crawl_commoncrawl():
    try:
        indexes = cc_indexes()
    except Exception as exc:
        return [], [{"source": "commoncrawl", "target": "collinfo", "error": f"{type(exc).__name__}: {exc}"}]

    tasks = []
    selected_counts = {}
    for target, (first_year, last_year) in CC_TARGET_WINDOWS.items():
        selected = [idx for idx in indexes if first_year <= idx["_year"] <= last_year]
        selected_counts[target] = len(selected)
        tasks.extend((idx, target) for idx in selected)

    print(f"commoncrawl tasks={len(tasks)} selected={selected_counts}", flush=True)
    found = {}
    errors = []
    completed = 0
    with ThreadPoolExecutor(max_workers=CC_WORKERS) as pool:
        futures = {pool.submit(cc_one, idx, target): (idx.get("id", ""), target) for idx, target in tasks}
        for future in as_completed(futures):
            rows, err = future.result()
            completed += 1
            if err:
                errors.append(err)
            for row in rows:
                found[(row["original"], row["timestamp"], row["digest"])] = row
            if rows or completed % 50 == 0:
                index_id, target = futures[future]
                print(f"cc {completed}/{len(tasks)} {index_id} {target} rows={len(rows)}", flush=True)
    return sorted(found.values(), key=lambda r: (r["original"], r["timestamp"])), errors


def write_csv(path: Path, rows: list[dict], fields: list[str]):
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)


def by_host(rows):
    c = Counter()
    for row in rows:
        c[urllib.parse.urlparse(row.get("original", "")).hostname or ""] += 1
    return dict(sorted(c.items()))


def main():
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
        "crawler_version": "3.1",
        "wayback_unique_records": len(wb_rows),
        "commoncrawl_unique_records": len(cc_rows),
        "wayback_by_host": by_host(wb_rows),
        "commoncrawl_by_host": by_host(cc_rows),
        "errors": wb_errors + cc_errors,
        "wayback_targets": WAYBACK_TARGETS,
        "commoncrawl_windows": {k: list(v) for k, v in CC_TARGET_WINDOWS.items()},
        "notes": [
            "Candidate domains are discovery-only until archived content proves they belong to 《齐鲁少年》.",
            "Archive timestamps are capture times and must not be used as newspaper publication dates.",
            "paper.cnssiot.cn is excluded because it was independently verified as 《山东青年报》.",
            "qlsn.cn and www.qlsn.cn are excluded because they were independently verified as 齐鲁三农网.",
        ],
    }
    (OUT / "crawl_report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
