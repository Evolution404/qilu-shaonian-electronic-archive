#!/usr/bin/env python3
"""Recover issue-numbered child pages exposed by archived official qlsn.com homepages.

Inputs are not guessed: every target URL/issue comes from an archived official-home anchor.
We try exact/closest Wayback forms and commit compact metadata/excerpts only.
"""
from __future__ import annotations

import csv
import html
import json
import re
import time
import urllib.parse
import urllib.request
from pathlib import Path

import mine_legacy_2004_2010_inventory as mine

ROOT=Path(__file__).resolve().parents[1]
SRC=ROOT/"data"/"legacy_2004_2010_home_issue_context"/"issue_links.csv"
OUT=ROOT/"data"/"legacy_2007_2009_child_recovery"
OUT.mkdir(parents=True,exist_ok=True)
UA="qilu-shaonian-electronic-archive/child-recovery-2007-2009-1.0"
TIMEOUT=28

SEED_TS={"2007_nov":"20071103154018","2009_aug":"20090830081307","2009_dec":"20091225202932"}
SOURCE_RE=re.compile(r"(?:【?来源】?\s*[：:]?\s*)([^【]{1,100})")
AUTHOR_RE=re.compile(r"(?:【?作者】?\s*[：:]?\s*)([^【]{1,80})")
DATE_RE=re.compile(r"(?:【?日期】?\s*[：:]?\s*)((?:200[4-9]|2010)[-/.]\d{1,2}[-/.]\d{1,2})")


def get(url, accept="text/html,*/*"):
    req=urllib.request.Request(url,headers={"User-Agent":UA,"Accept":accept})
    with urllib.request.urlopen(req,timeout=TIMEOUT) as r:
        return r.read(6*1024*1024),r.geturl(),r.headers.get_content_type()

def available(original,ts):
    qs=urllib.parse.urlencode({"url":original,"timestamp":ts})
    raw,_,_=get("https://archive.org/wayback/available?"+qs,"application/json")
    data=json.loads(raw.decode("utf-8","replace"));c=(data.get("archived_snapshots") or {}).get("closest") or {}
    return c.get("timestamp","") if c.get("available") else "",c.get("url","") if c.get("available") else ""

def variants(original,seed_ts):
    out=[]
    for delta_ts in (seed_ts, seed_ts[:8]+"000000"):
        for mod in ("id_","if_",""):
            out.append(f"https://web.archive.org/web/{delta_ts}{mod}/{original}")
    try:
        ts,u=available(original,seed_ts)
        if u:
            out.append(re.sub(r"/web/(\d+)(?:[a-z_]+)?/",r"/web/\1id_/",u,count=1));out.append(u)
    except Exception:
        pass
    return list(dict.fromkeys(out))

def parse(raw,original,used):
    text,enc=mine.decode(raw);p=mine.Parser();p.feed(text)
    visible=re.sub(r"\s+"," ",html.unescape(" ".join(p.visible))).strip();title=" ".join(p.title).strip()
    dm=DATE_RE.search(visible);sm=SOURCE_RE.search(visible);am=AUTHOR_RE.search(visible)
    issues=mine.issue_candidates(visible)
    media=[]
    for kind,src in p.media:
        u=mine.unwayback(original,src);hp=urllib.parse.urlsplit(u)
        if (hp.hostname or "").lower() in {"qlsn.com","www.qlsn.com"} and mine.ABS_MEDIA_RE.search(hp.path):media.append(u)
    return {"resolved_archive_url":used,"title":title,"site_date":dm.group(1) if dm else "","author":am.group(1).strip() if am else "","source_text":sm.group(1).strip() if sm else "","body_issue_numbers":"|".join(i for i,_ in issues),"body_issue_confidence":"|".join(c for _,c in issues),"encoding":enc,"media_urls":"|".join(dict.fromkeys(media)),"excerpt":visible[:1400]}

def main():
    targets=[];seen=set()
    for r in csv.DictReader(SRC.open(newline="",encoding="utf-8")):
        key=(r["issue_number"],r["child_url"])
        if key in seen:continue
        seen.add(key);targets.append(r)
    results=[]
    for i,r in enumerate(targets,1):
        rec={"issue_number":r["issue_number"],"anchor_text":r["anchor_text"],"displayed_nearby_dates":r["nearby_dates"],"child_url":r["child_url"],"seed_id":r["seed_id"],"seed_archive_url":r["seed_archive_url"],"status":"unrecovered","resolved_archive_url":"","title":"","site_date":"","author":"","source_text":"","body_issue_numbers":"","body_issue_confidence":"","encoding":"","media_urls":"","excerpt":"","attempt_count":"0","errors":""}
        errs=[];count=0
        for u in variants(r["child_url"],SEED_TS.get(r["seed_id"],"20091225202932")):
            count+=1
            try:
                raw,final,ctype=get(u)
                if "html" not in ctype and b"<html" not in raw[:1000].lower():raise ValueError(f"not html: {ctype}")
                parsed=parse(raw,r["child_url"],final);rec.update(parsed);rec["status"]="recovered";break
            except Exception as exc:
                errs.append(f"{type(exc).__name__}:{exc}")
                time.sleep(0.15)
        rec["attempt_count"]=str(count);rec["errors"]=" | ".join(errs[-5:]);results.append(rec)
        print(f"{i}/{len(targets)} issue={rec['issue_number']} status={rec['status']} body={rec['body_issue_numbers']} title={rec['title'][:40]}",flush=True)
    fields=["issue_number","anchor_text","displayed_nearby_dates","child_url","seed_id","seed_archive_url","status","resolved_archive_url","title","site_date","author","source_text","body_issue_numbers","body_issue_confidence","encoding","media_urls","excerpt","attempt_count","errors"]
    with (OUT/"pages.csv").open("w",newline="",encoding="utf-8") as f:w=csv.DictWriter(f,fieldnames=fields);w.writeheader();w.writerows(results)
    recovered=[r for r in results if r["status"]=="recovered"]
    explicit=[r for r in recovered if r["issue_number"] in (r["body_issue_numbers"] or "").split("|")]
    report={"official_child_targets":len(targets),"recovered_pages":len(recovered),"unrecovered_pages":len(results)-len(recovered),"pages_body_confirms_anchor_issue":len(explicit),"confirmed_issues":sorted({r["issue_number"] for r in explicit},key=int),"recovered_issues":sorted({r["issue_number"] for r in recovered},key=int),"pages_with_media_refs":sum(bool(r["media_urls"]) for r in recovered),"notes":["Every target URL is sourced from an archived official qlsn.com homepage anchor.","A recovered page is promoted to verified issue content only when body/source context confirms the anchor issue or equivalent explicit identity.","No full article or image binaries are committed."]}
    (OUT/"report.json").write_text(json.dumps(report,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    print(json.dumps(report,ensure_ascii=False,indent=2),flush=True);return 0
if __name__=="__main__":raise SystemExit(main())
