#!/usr/bin/env python3
"""Recover the contiguous qlsn.com article_view IDs 216-221 associated with issue 745."""
from __future__ import annotations

import csv
import json
import re
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "data" / "legacy_recovery" / "issue745_siblings"
OUT.mkdir(parents=True, exist_ok=True)
UA = "qilu-shaonian-745-sibling-recovery/1.0 (+https://github.com/Evolution404/qilu-shaonian-electronic-archive)"
TARGET_TS = "20070624"


def get(url: str, limit=4*1024*1024):
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=18) as r:
        raw = r.read(limit + 1)
        if len(raw) > limit:
            raise ValueError("response too large")
        return raw, r.geturl(), r.headers.get("Content-Type", "")


def closest(original: str):
    api = "https://archive.org/wayback/available?" + urllib.parse.urlencode({"url": original, "timestamp": TARGET_TS})
    raw, _, _ = get(api, 1024*1024)
    data = json.loads(raw.decode("utf-8", "replace"))
    c = (data.get("archived_snapshots") or {}).get("closest") or {}
    if not c.get("available") or not c.get("url"):
        return "", ""
    return re.sub(r"/web/(\d+)/", r"/web/\1id_/", c["url"], count=1), c.get("timestamp", "")


def decode(raw: bytes):
    best = None
    for enc in ("gb18030", "utf-8"):
        try:
            s = raw.decode(enc, "replace")
            score = s.count("\ufffd")
            if best is None or score < best[0]:
                best = (score, s)
        except Exception:
            pass
    return best[1] if best else raw.decode("utf-8", "replace")


def visible(text: str):
    text = re.sub(r"(?is)<script.*?</script>|<style.*?</style>", " ", text)
    text = re.sub(r"(?s)<[^>]+>", " ", text)
    text = re.sub(r"&nbsp;|&#160;", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def main():
    rows = []
    for aid in range(216, 222):
        original = f"http://www.qlsn.com/article_view.asp?id={aid}"
        row = {"article_id": str(aid), "original_url": original, "snapshot_timestamp": "", "archive_url": "", "title": "", "site_date": "", "source_text": "", "issue_number": "", "verified_issue745": "no", "error": ""}
        try:
            snap, ts = closest(original)
            if not snap:
                row["error"] = "no closest snapshot"
                rows.append(row); continue
            raw, final, _ = get(snap)
            text = decode(raw)
            v = visible(text)
            tm = re.search(r"浏览文章\s+(.{1,100}?)\s+【日期】", v)
            dm = re.search(r"【日期】\s*([^【]{1,30})", v)
            sm = re.search(r"【来源】\s*([^【]{1,100})", v)
            im = re.search(r"齐鲁少年[（(]\s*(\d{2,5})\s*期[）)]", v)
            row.update({
                "snapshot_timestamp": ts,
                "archive_url": final,
                "title": tm.group(1).strip() if tm else "",
                "site_date": dm.group(1).strip() if dm else "",
                "source_text": sm.group(1).strip() if sm else "",
                "issue_number": im.group(1) if im else "",
                "verified_issue745": "yes" if im and im.group(1) == "745" else "no",
            })
        except Exception as e:
            row["error"] = f"{type(e).__name__}: {e}"[:1000]
        rows.append(row)

    fields = ["article_id", "original_url", "snapshot_timestamp", "archive_url", "title", "site_date", "source_text", "issue_number", "verified_issue745", "error"]
    with (OUT / "articles.csv").open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields); w.writeheader(); w.writerows(rows)
    report = {
        "ids": "216-221",
        "snapshots_recovered": sum(bool(r["archive_url"]) for r in rows),
        "verified_issue745": sum(r["verified_issue745"] == "yes" for r in rows),
        "missing_ids": [r["article_id"] for r in rows if not r["archive_url"]],
        "verified_titles": [r["title"] for r in rows if r["verified_issue745"] == "yes"],
        "notes": ["Only metadata is committed; full article bodies are not persisted."]
    }
    (OUT / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2), flush=True)


if __name__ == "__main__":
    main()
