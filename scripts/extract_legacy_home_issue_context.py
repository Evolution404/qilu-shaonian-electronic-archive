#!/usr/bin/env python3
"""Extract issue-link context and nearby displayed dates from archived official qlsn.com homepages."""
from __future__ import annotations

import csv
import html
import json
import re
import urllib.parse
import urllib.request
from html.parser import HTMLParser
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/"data"/"legacy_2004_2010_home_issue_context"
OUT.mkdir(parents=True,exist_ok=True)
UA="qilu-shaonian-electronic-archive/home-issue-context-1.0"
SEEDS=[
    ("2007_nov","https://web.archive.org/web/20071103154018id_/http://www.qlsn.com:80/","http://www.qlsn.com/"),
    ("2009_aug","https://web.archive.org/web/20090830081307id_/http://www.qlsn.com:80/","http://www.qlsn.com/"),
    ("2009_dec","https://web.archive.org/web/20091225202932id_/http://www.qlsn.com:80/","http://www.qlsn.com/"),
]
ISSUE_RE=re.compile(r"(?<!\d)(\d{3,4})\s*期")
DATE_RE=re.compile(r"(?<!\d)((?:200[4-9]|2010)[-/年]\d{1,2}[-/月]\d{1,2}日?|\d{1,2}[-/]\d{1,2})(?!\d)")
ROUTE_RE=re.compile(r"(?i)(?:article|news|announce)_view\.asp\?id=\d+")

class P(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True); self.events=[]; self.href=None; self.anchor=[]
    def handle_starttag(self,tag,attrs):
        if tag.lower()=="a":
            d={k.lower():(v or "") for k,v in attrs}
            if d.get("href"):
                self.href=d["href"]; self.anchor=[]
    def handle_data(self,data):
        t=" ".join(data.split())
        if not t:return
        if self.href is not None:self.anchor.append(t)
        self.events.append(("text",t))
    def handle_endtag(self,tag):
        if tag.lower()=="a" and self.href is not None:
            text=" ".join(self.anchor).strip(); self.events.append(("link",self.href,text)); self.href=None; self.anchor=[]

def fetch(url):
    req=urllib.request.Request(url,headers={"User-Agent":UA,"Accept":"text/html,*/*"})
    with urllib.request.urlopen(req,timeout=30) as r:return r.read(6*1024*1024)

def decode(raw):
    best=None
    for enc in ("utf-8","gb18030","big5"):
        s=raw.decode(enc,"replace"); q=s.count("\ufffd")
        if best is None or q<best[0]:best=(q,enc,s)
    return best[1],best[2]

def main():
    rows=[]; seed_summary=[]
    for seed_id,archive,base in SEEDS:
        try:
            raw=fetch(archive); enc,text=decode(raw); p=P(); p.feed(text)
            ev=p.events
            hits=0
            for i,e in enumerate(ev):
                if e[0]!="link":continue
                href,anchor=e[1],e[2]
                issues=ISSUE_RE.findall(anchor)
                if not issues:continue
                resolved=urllib.parse.urljoin(base,html.unescape(href))
                if not ROUTE_RE.search(resolved):continue
                before=" ".join(x[1] if x[0]=="text" else (x[2] if len(x)>2 else "") for x in ev[max(0,i-8):i])
                after=" ".join(x[1] if x[0]=="text" else (x[2] if len(x)>2 else "") for x in ev[i+1:i+9])
                context=re.sub(r"\s+"," ",(before+" [LINK] "+anchor+" "+after)).strip()
                dates=DATE_RE.findall(context)
                for issue in sorted(set(issues),key=int):
                    rows.append({"seed_id":seed_id,"seed_archive_url":archive,"encoding":enc,"issue_number":issue,"anchor_text":anchor,"child_url":resolved,"nearby_dates":"|".join(dict.fromkeys(dates)),"context":context[:1000]})
                    hits+=1
            seed_summary.append({"seed_id":seed_id,"link_issue_rows":hits,"error":""})
        except Exception as exc:
            seed_summary.append({"seed_id":seed_id,"link_issue_rows":0,"error":f"{type(exc).__name__}: {exc}"})
    rows.sort(key=lambda r:(int(r["issue_number"]),r["seed_id"],r["child_url"]))
    with (OUT/"issue_links.csv").open("w",newline="",encoding="utf-8") as f:
        w=csv.DictWriter(f,fieldnames=["seed_id","seed_archive_url","encoding","issue_number","anchor_text","child_url","nearby_dates","context"]);w.writeheader();w.writerows(rows)
    report={"issue_link_rows":len(rows),"distinct_issues":sorted({r["issue_number"] for r in rows},key=int),"rows_with_nearby_date":sum(bool(r["nearby_dates"]) for r in rows),"seed_summary":seed_summary,"notes":["Nearby dates are displayed-page context, not automatically newspaper publication dates.","Issue numbers come directly from archived official-home anchor text."]}
    (OUT/"report.json").write_text(json.dumps(report,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    print(json.dumps(report,ensure_ascii=False,indent=2),flush=True);return 0
if __name__=="__main__":raise SystemExit(main())
