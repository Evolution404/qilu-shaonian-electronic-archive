#!/usr/bin/env python3
"""Mine 2009-2010 issue anchors from archived qlsn.com/sdbook forum pages.

The forum/listing captures are often from 2011 but preserve historical post timestamps and
issue-numbered titles from 2009-2010. Capture time and post time are kept separate.
"""
from __future__ import annotations

import csv
import html
import json
import re
import urllib.request
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
INV=ROOT/"data"/"archive_crawl"/"wayback_urls.csv"
OUT=ROOT/"data"/"legacy_2009_2010_sdbook"
OUT.mkdir(parents=True,exist_ok=True)
UA="qilu-shaonian-electronic-archive/sdbook-2009-2010-1.0"
ISSUE_RE=re.compile(r"(?<!\d)(\d{3,4})\s*期")
POST_DATE_RE=re.compile(r"(?<!\d)(1[01]|0?\d)-(0?[1-9]|1[0-2])-(0?[1-9]|[12]\d|3[01])\s+\d{1,2}:\d{2}:\d{2}")
# Match a modest window around a 2010-style two-digit post timestamp.
WINDOW_RE=re.compile(r"(.{0,220}?)(10|09)-(\d{1,2})-(\d{1,2})\s+\d{1,2}:\d{2}:\d{2}(.{0,180})",re.S)
HREF_RE=re.compile(r'''(?is)<a\b[^>]*href=["']([^"']+)["'][^>]*>(.*?)</a>''')
TAG_RE=re.compile(r"(?is)<[^>]+>")


def fetch(url):
    req=urllib.request.Request(url,headers={"User-Agent":UA,"Accept":"text/html,*/*"})
    with urllib.request.urlopen(req,timeout=25) as r:return r.read(8*1024*1024)

def decode(raw):
    best=None
    for enc in ("utf-8","gb18030","big5"):
        s=raw.decode(enc,"replace");q=s.count("\ufffd")
        if best is None or q<best[0]:best=(q,enc,s)
    return best[1],best[2]

def clean(s):return re.sub(r"\s+"," ",html.unescape(TAG_RE.sub(" ",s))).strip()

def main():
    inv=list(csv.DictReader(INV.open(newline="",encoding="utf-8")))
    pages=[];seen=set()
    for r in inv:
        u=r.get("original","")
        if "qlsn.com/sdbook/" not in u.lower() or r.get("statuscode")!="200" or "html" not in (r.get("mimetype") or "").lower():continue
        k=(u,r.get("digest", ""))
        if k in seen:continue
        seen.add(k);pages.append(r)
    posts=[];page_rows=[]
    for idx,r in enumerate(pages,1):
        pr={"capture_timestamp":r.get("timestamp", ""),"original":r.get("original", ""),"archive_url":r.get("archive_url", ""),"encoding":"","historical_windows":0,"issue_mentions":0,"error":""}
        try:
            raw=fetch(pr["archive_url"]);enc,text=decode(raw);pr["encoding"]=enc
            # Flatten while retaining anchor labels/URLs in a token stream.
            links=[]
            for href,inner in HREF_RE.findall(text):
                label=clean(inner)
                if label:links.append((href,label))
            visible=clean(text)
            windows=[]
            for m in WINDOW_RE.finditer(visible):
                year="20"+m.group(2);month=int(m.group(3));day=int(m.group(4));ctx=re.sub(r"\s+"," ",m.group(0)).strip()
                windows.append((f"{year}-{month:02d}-{day:02d}",ctx))
            pr["historical_windows"]=len(windows)
            # Prefer anchors whose own title carries an issue and associate nearest visible timestamp if discoverable.
            for href,label in links:
                issues=ISSUE_RE.findall(label)
                if not issues:continue
                post_date=""
                pos=visible.find(label)
                around=visible[max(0,pos-260):pos+500] if pos>=0 else ""
                dm=re.search(r"(?<!\d)(10|09)-(\d{1,2})-(\d{1,2})\s+\d{1,2}:\d{2}:\d{2}",around)
                if dm:post_date=f"20{dm.group(1)}-{int(dm.group(2)):02d}-{int(dm.group(3)):02d}"
                for issue in sorted(set(issues),key=int):
                    posts.append({"issue_number":issue,"post_date":post_date,"title":label,"post_url":href,"page_original":pr["original"],"page_archive_url":pr["archive_url"],"evidence_kind":"issue_in_forum_anchor","context":around[:900]})
            # Also inspect timestamp windows because some issue labels are plain text rather than links.
            for post_date,ctx in windows:
                for issue in sorted(set(ISSUE_RE.findall(ctx)),key=int):
                    posts.append({"issue_number":issue,"post_date":post_date,"title":"","post_url":"","page_original":pr["original"],"page_archive_url":pr["archive_url"],"evidence_kind":"issue_in_dated_forum_context","context":ctx[:900]})
            pr["issue_mentions"]=sum(1 for p in posts if p["page_archive_url"]==pr["archive_url"])
        except Exception as exc:pr["error"]=f"{type(exc).__name__}: {exc}"
        page_rows.append(pr);print(f"{idx}/{len(pages)} windows={pr['historical_windows']} issues={pr['issue_mentions']} err={pr['error'][:50]}",flush=True)
    # Restrict dated contexts to target years where date is known; undated anchor labels stay as metadata because page is official.
    filtered=[];seenp=set()
    for p in posts:
        d=p["post_date"]
        if d and not d.startswith(("2009-","2010-")):continue
        key=(p["issue_number"],p["post_date"],p["title"],p["post_url"],p["evidence_kind"],p["page_archive_url"])
        if key in seenp:continue
        seenp.add(key);filtered.append(p)
    filtered.sort(key=lambda p:(int(p["issue_number"]),p["post_date"],p["title"],p["post_url"]))
    with (OUT/"issue_posts.csv").open("w",newline="",encoding="utf-8") as f:
        w=csv.DictWriter(f,fieldnames=["issue_number","post_date","title","post_url","page_original","page_archive_url","evidence_kind","context"]);w.writeheader();w.writerows(filtered)
    with (OUT/"pages.csv").open("w",newline="",encoding="utf-8") as f:
        w=csv.DictWriter(f,fieldnames=["capture_timestamp","original","archive_url","encoding","historical_windows","issue_mentions","error"]);w.writeheader();w.writerows(page_rows)
    issues=sorted({p["issue_number"] for p in filtered},key=int)
    report={"sdbook_html_states":len(pages),"pages_fetch_ok":sum(not p["error"] for p in page_rows),"issue_evidence_rows":len(filtered),"distinct_issues":issues,"dated_2009_rows":sum(p["post_date"].startswith("2009-") for p in filtered),"dated_2010_rows":sum(p["post_date"].startswith("2010-") for p in filtered),"issues_with_explicit_post_date":sorted({p["issue_number"] for p in filtered if p["post_date"]},key=int),"notes":["Forum capture year may be 2011; historical post dates are preserved separately.","A forum request for a sample copy is issue-existence metadata, not an electronic newspaper page.","No newspaper publication date is inferred from the forum post date."]}
    (OUT/"report.json").write_text(json.dumps(report,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    print(json.dumps(report,ensure_ascii=False,indent=2),flush=True);return 0
if __name__=="__main__":raise SystemExit(main())
