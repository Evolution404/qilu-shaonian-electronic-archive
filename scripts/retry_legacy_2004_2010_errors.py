#!/usr/bin/env python3
"""Retry transiently failed qlsn.com legacy-page captures with alternate Wayback forms."""
from __future__ import annotations

import csv
import html
import json
import re
import time
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import mine_legacy_2004_2010_inventory as mine

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "data" / "legacy_2004_2010_inventory" / "pages.csv"
OUT = ROOT / "data" / "legacy_2004_2010_retry"
OUT.mkdir(parents=True, exist_ok=True)
UA = "qilu-shaonian-electronic-archive/legacy-retry-1.0"
TIMEOUT = 25
WORKERS = 6


def fetch_once(url: str) -> tuple[bytes, str]:
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "text/html,*/*;q=0.5"})
    with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
        return r.read(5 * 1024 * 1024), r.geturl()


def available(original: str, timestamp: str) -> str:
    qs = urllib.parse.urlencode({"url": original, "timestamp": timestamp or "20091231120000"})
    req = urllib.request.Request("https://archive.org/wayback/available?" + qs, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
        data = json.loads(r.read(1_000_000).decode("utf-8", "replace"))
    c = (data.get("archived_snapshots") or {}).get("closest") or {}
    return c.get("url", "") if c.get("available") else ""


def candidates(row: dict) -> list[str]:
    ts = row.get("timestamp", "")
    original = row.get("original", "")
    urls = [row.get("archive_url", "")]
    if ts and original:
        for mod in ("id_", "if_", ""):
            urls.append(f"https://web.archive.org/web/{ts}{mod}/{original}")
    try:
        a = available(original, ts)
        if a:
            urls.append(re.sub(r"/web/(\d+)(?:[a-z_]+)?/", r"/web/\1id_/", a, count=1))
            urls.append(a)
    except Exception:
        pass
    out=[]
    for u in urls:
        if u and u not in out:
            out.append(u)
    return out


def parse_visible(raw: bytes, row: dict, used_url: str) -> tuple[dict,list[dict],list[dict]]:
    text, enc = mine.decode(raw)
    p = mine.Parser(); p.feed(text)
    visible = re.sub(r"\s+", " ", html.unescape(" ".join(p.visible))).strip()
    title = " ".join(p.title).strip()
    dm = mine.DATE_RE.search(visible)
    site_date = dm.group(1) if dm else ""
    issue_pairs = mine.issue_candidates(visible)
    year = mine.classify_year(visible, site_date)
    page = {
        "timestamp": row.get("timestamp", ""), "original": row.get("original", ""),
        "requested_archive_url": row.get("archive_url", ""), "used_archive_url": used_url,
        "title": title, "site_date": site_date, "content_year": year,
        "issue_numbers": "|".join(i for i,_ in issue_pairs),
        "issue_confidence": "|".join(c for _,c in issue_pairs),
        "encoding": enc, "link_count": str(len(p.links)), "media_count": str(len(p.media)),
        "excerpt": visible[:900], "error": "",
    }
    issues=[]
    for issue, conf in issue_pairs:
        pos=visible.find(issue)
        issues.append({"issue_number":issue,"confidence":conf,"content_year":year,"site_date":site_date,"title":title,"original":row.get("original", ""),"archive_url":used_url,"context":visible[max(0,pos-150):pos+260] if pos>=0 else visible[:410]})
    media=[]
    for kind, src in p.media:
        u = mine.unwayback(row.get("original", ""), src)
        hp=urllib.parse.urlsplit(u)
        if (hp.hostname or "").lower() not in {"qlsn.com","www.qlsn.com"} or not mine.ABS_MEDIA_RE.search(hp.path):
            continue
        media.append({"parent_original":row.get("original", ""),"parent_archive_url":used_url,"parent_content_year":year,"parent_issue_numbers":page["issue_numbers"],"kind":kind,"media_original":u})
    return page,issues,media


def retry(row: dict):
    errs=[]
    for u in candidates(row):
        for attempt in range(2):
            try:
                raw, final = fetch_once(u)
                return (*parse_visible(raw,row,final), "")
            except Exception as exc:
                errs.append(f"{u}: {type(exc).__name__}: {exc}")
                if attempt == 0:
                    time.sleep(0.6)
    return ({"timestamp":row.get("timestamp", ""),"original":row.get("original", ""),"requested_archive_url":row.get("archive_url", ""),"used_archive_url":"","title":"","site_date":"","content_year":"","issue_numbers":"","issue_confidence":"","encoding":"","link_count":"","media_count":"","excerpt":"","error":" | ".join(errs[-6:])}, [], [], "failed")


def write(path, rows, fields):
    with path.open("w",newline="",encoding="utf-8") as f:
        w=csv.DictWriter(f,fieldnames=fields,extrasaction="ignore"); w.writeheader(); w.writerows(rows)


def main() -> int:
    rows=list(csv.DictReader(SRC.open(newline="",encoding="utf-8")))
    failed=[r for r in rows if r.get("error")]
    pages=[]; issues=[]; media=[]
    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        futs=[pool.submit(retry,r) for r in failed]
        for n,fut in enumerate(as_completed(futs),1):
            p,ih,m,_=fut.result(); pages.append(p); issues.extend(ih); media.extend(m)
            print(f"retry {n}/{len(futs)} ok={not bool(p.get('error'))} issue={p.get('issue_numbers')} {p.get('original')}",flush=True)
    pages.sort(key=lambda r:r.get("original", "")); issues.sort(key=lambda r:(int(r.get("issue_number") or 999999),r.get("original", "")))
    write(OUT/"pages.csv",pages,["timestamp","original","requested_archive_url","used_archive_url","title","site_date","content_year","issue_numbers","issue_confidence","encoding","link_count","media_count","excerpt","error"])
    write(OUT/"issue_hits.csv",issues,["issue_number","confidence","content_year","site_date","title","original","archive_url","context"])
    write(OUT/"media_refs.csv",media,["parent_original","parent_archive_url","parent_content_year","parent_issue_numbers","kind","media_original"])
    report={
        "original_failed_pages":len(failed),
        "retry_successes":sum(not p.get("error") for p in pages),
        "retry_failures":sum(bool(p.get("error")) for p in pages),
        "new_issue_hit_rows":len(issues),
        "distinct_issue_numbers":sorted({r["issue_number"] for r in issues},key=int),
        "target_year_pages":sum(bool(p.get("content_year")) and 2004<=int(p["content_year"])<=2010 for p in pages),
        "notes":["This pass retries transport failures; it does not reinterpret confirmed 404/no-snapshot results as recoverable.","Alternate raw/iframe/closest Wayback URLs are tried. No archived article bodies are committed."],
    }
    (OUT/"report.json").write_text(json.dumps(report,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    print(json.dumps(report,ensure_ascii=False,indent=2),flush=True)
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
