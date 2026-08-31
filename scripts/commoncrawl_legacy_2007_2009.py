#!/usr/bin/env python3
"""Recover official 2007-2009 qlsn.com issue child pages from Common Crawl.

Targets come only from archived official qlsn.com homepage anchors already stored in
`data/legacy_2004_2010_home_issue_context/issue_links.csv`.  This pass is independent
of Wayback: it queries exact URLs in historical Common Crawl indexes, retrieves WARC
records when available, and commits only metadata/text excerpts/hashes/media URLs.
"""
from __future__ import annotations

import csv
import hashlib
import io
import json
import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from urllib.parse import urlencode, urljoin

import requests
from bs4 import BeautifulSoup
from warcio.archiveiterator import ArchiveIterator

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "data" / "legacy_2004_2010_home_issue_context" / "issue_links.csv"
OUT = ROOT / "data" / "legacy_2007_2009_commoncrawl"
OUT.mkdir(parents=True, exist_ok=True)
UA = "qilu-shaonian-legacy-commoncrawl/1.0 (+https://github.com/Evolution404/qilu-shaonian-electronic-archive)"
S = requests.Session()
S.headers.update({"User-Agent": UA})
YEAR_RE = re.compile(r"CC-MAIN-(20\d{2})")
MEDIA_RE = re.compile(r"(?i)\.(?:jpe?g|png|gif|pdf)(?:\?|$)")


def get_json(url, timeout=45):
    r = S.get(url, timeout=timeout)
    r.raise_for_status()
    return r.json()


def indexes():
    rows = get_json("https://index.commoncrawl.org/collinfo.json")
    out = []
    for x in rows:
        m = YEAR_RE.search(x.get("id", ""))
        if m and 2008 <= int(m.group(1)) <= 2011 and x.get("cdx-api"):
            out.append(x)
    return sorted(out, key=lambda x: x["id"])


def targets():
    seen = set()
    out = []
    with SRC.open(encoding="utf-8-sig", newline="") as f:
        for row in csv.DictReader(f):
            issue = row.get("issue_number", "").strip()
            url = row.get("child_url", "").strip()
            seed = row.get("seed_id", "")
            if not issue.isdigit() or not url or (url, issue) in seen:
                continue
            n = int(issue)
            # 780-900 are the official-home issue paths whose bodies Wayback failed to recover.
            if 780 <= n <= 900:
                seen.add((url, issue))
                out.append({"issue_number": issue, "url": url, "anchor_text": row.get("anchor_text", ""), "seed_id": seed})
    return out


def query(index, target):
    params = urlencode({"url": target["url"], "output": "json", "filter": "status:200", "matchType": "exact"})
    api = index["cdx-api"] + "?" + params
    try:
        r = S.get(api, timeout=45)
        r.raise_for_status()
        hits = []
        for line in r.text.splitlines():
            if not line.strip():
                continue
            d = json.loads(line)
            hits.append({
                **target,
                "index": index["id"],
                "timestamp": d.get("timestamp", ""),
                "captured_url": d.get("url", ""),
                "mime": d.get("mime", d.get("mime-detected", "")),
                "digest": d.get("digest", ""),
                "filename": d.get("filename", ""),
                "offset": d.get("offset", ""),
                "length": d.get("length", ""),
            })
        return hits, ""
    except Exception as e:
        return [], f"{type(e).__name__}: {e}"


def recover(row):
    if not row.get("filename") or not row.get("offset") or not row.get("length"):
        return {**row, "recovered": "no", "error": "missing WARC locator"}
    try:
        off, length = int(row["offset"]), int(row["length"])
        if length > 20 * 1024 * 1024:
            raise ValueError(f"WARC range too large: {length}")
        url = "https://data.commoncrawl.org/" + row["filename"]
        r = S.get(url, headers={"Range": f"bytes={off}-{off + length - 1}"}, timeout=60)
        r.raise_for_status()
        record = next(ArchiveIterator(io.BytesIO(r.content)))
        body = record.content_stream().read(8 * 1024 * 1024)
        sha = hashlib.sha256(body).hexdigest()
        text = body.decode("gb18030", "replace")
        if text.count("�") > max(10, len(text) // 100):
            text = body.decode("utf-8", "replace")
        soup = BeautifulSoup(text, "html.parser")
        plain = re.sub(r"\s+", " ", soup.get_text(" ", strip=True))
        media = []
        for tag in soup.find_all(["img", "a"]):
            attr = tag.get("src") or tag.get("href") or ""
            if attr and MEDIA_RE.search(attr):
                media.append(urljoin(row.get("captured_url") or row["url"], attr))
        return {
            **row,
            "recovered": "yes",
            "body_bytes": len(body),
            "sha256": sha,
            "text_confirms_issue": "yes" if re.search(rf"第?\s*{re.escape(row['issue_number'])}\s*期|{re.escape(row['issue_number'])}\s*期", plain) else "no",
            "media_refs": "|".join(dict.fromkeys(media)),
            "excerpt": plain[:1000],
            "error": "",
        }
    except Exception as e:
        return {**row, "recovered": "no", "error": f"{type(e).__name__}: {e}"}


def write_csv(path, rows, fields):
    with path.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        w.writeheader(); w.writerows(rows)


def main():
    idxs, tgts = indexes(), targets()
    errors, hits = [], []
    with ThreadPoolExecutor(max_workers=6) as pool:
        futs = {pool.submit(query, i, t): (i, t) for i in idxs for t in tgts}
        for fut in as_completed(futs):
            found, err = fut.result()
            hits.extend(found)
            if err:
                i, t = futs[fut]
                errors.append({"index": i["id"], "url": t["url"], "error": err})
    uniq = {}
    for h in hits:
        uniq[(h["issue_number"], h["timestamp"], h["filename"], h["offset"])] = h
    hits = sorted(uniq.values(), key=lambda x: (int(x["issue_number"]), x["timestamp"]))
    recovered = []
    with ThreadPoolExecutor(max_workers=4) as pool:
        for r in pool.map(recover, hits):
            recovered.append(r)
            time.sleep(0.03)

    write_csv(OUT / "index_hits.csv", hits, ["issue_number","url","anchor_text","seed_id","index","timestamp","captured_url","mime","digest","filename","offset","length"])
    write_csv(OUT / "recovered.csv", recovered, ["issue_number","url","anchor_text","index","timestamp","captured_url","mime","recovered","body_bytes","sha256","text_confirms_issue","media_refs","excerpt","error"])
    write_csv(OUT / "errors.csv", errors, ["index","url","error"])
    confirmed = sorted({int(x["issue_number"]) for x in recovered if x.get("recovered") == "yes" and x.get("text_confirms_issue") == "yes"})
    report = {
        "historical_indexes_selected": [x["id"] for x in idxs],
        "official_child_targets": len(tgts),
        "index_hits": len(hits),
        "warc_recovered": sum(x.get("recovered") == "yes" for x in recovered),
        "issue_confirmed_warc_pages": sum(x.get("text_confirms_issue") == "yes" for x in recovered),
        "confirmed_issues": confirmed,
        "pages_with_media_refs": sum(bool(x.get("media_refs")) for x in recovered),
        "query_errors": len(errors),
        "notes": [
            "Targets originate from archived official qlsn.com homepage anchors, not guessed article IDs.",
            "This is an independent Common Crawl recovery after Wayback body recovery returned 0/24.",
            "Only metadata, excerpts, media URLs and hashes are committed; no third-party newspaper binaries are stored."
        ]
    }
    (OUT / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))

if __name__ == "__main__":
    main()
