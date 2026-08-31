#!/usr/bin/env python3
"""Recover original-layout page assets from the historical szb.cnssiot.cn CMS.

Uses the single verified Wayback root snapshot as a seed, inspects its HTML/JS references,
queries CDX specifically for content/Img/API paths, and probes known public CMS endpoints.
Only URLs/metadata/text snippets are committed; newspaper image bytes are not.
"""
from __future__ import annotations

import csv
import html
import json
import re
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from html.parser import HTMLParser
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/"data"/"szb_recovery"; OUT.mkdir(parents=True,exist_ok=True)
UA="qilu-shaonian-szb-recovery/1.0 (+https://github.com/Evolution404/qilu-shaonian-electronic-archive)"
TIMEOUT=25
TS="20220928010853"
ORIGIN="http://szb.cnssiot.cn/"
SNAP=f"https://web.archive.org/web/{TS}id_/{ORIGIN}"

IMG_RE=re.compile(r"(?:https?://szb\.cnssiot\.cn/)?(?:Img|img)/[^\"'<>\s]+?\.(?:jpe?g|png)(?:\?[^\"'<>\s]*)?",re.I)
EDITION_RE=re.compile(r"content/(20\d{2}-\d{2}/\d{2})/edition(\d+)_([A-Z]\d+)\.html",re.I)
ARTICLE_RE=re.compile(r"content/(20\d{2}-\d{2}/\d{2})/(\d+)\.html",re.I)
ENDPOINT_RE=re.compile(r"[\"']((?:/api/|/jquery/)[^\"']+)[\"']",re.I)
MOBILE_RE=re.compile(r"mobile20\d{10,}[^\"'<>\s]*\.(?:jpe?g|png)",re.I)

class P(HTMLParser):
    def __init__(self): super().__init__(convert_charrefs=True); self.scripts=[]; self.links=[]; self.images=[]
    def handle_starttag(self,tag,attrs):
        d={k.lower():(v or "") for k,v in attrs}; tag=tag.lower()
        if tag=="script" and d.get("src"): self.scripts.append(d["src"])
        if tag=="a" and d.get("href"): self.links.append(d["href"])
        if tag=="img" and d.get("src"): self.images.append(d["src"])


def get(url,accept="*/*"):
    r=urllib.request.Request(url,headers={"User-Agent":UA,"Accept":accept})
    with urllib.request.urlopen(r,timeout=TIMEOUT) as x: return x.read(),x.geturl(),{k.lower():v for k,v in x.headers.items()}

def decode(raw,h=None):
    c=(h or {}).get("content-type","")
    m=re.search(r"charset=([\w.-]+)",c,re.I)
    encs=([m.group(1)] if m else [])+["utf-8","gb18030"]
    best=None
    for e in encs:
        try:
            t=raw.decode(e,"replace"); q=t.count("\ufffd")
            if best is None or q<best[0]: best=(q,t)
        except Exception: pass
    return best[1] if best else raw.decode("utf-8","replace")

def archive_for_original(url):
    if url.startswith("//"): url="http:"+url
    if url.startswith("/"): url=urllib.parse.urljoin(ORIGIN,url)
    return f"https://web.archive.org/web/{TS}id_/{url}"

def normalize_origin(base,raw):
    v=html.unescape(raw.strip())
    if v.startswith("//"): v="http:"+v
    return urllib.parse.urljoin(base,v)

def extract(text,source):
    rows=[]
    for m in IMG_RE.findall(text): rows.append(("image",m,source))
    for date,eid,page in EDITION_RE.findall(text): rows.append(("edition_route",f"http://szb.cnssiot.cn/content/{date}/edition{eid}_{page}.html",source))
    for date,aid in ARTICLE_RE.findall(text): rows.append(("article_route",f"http://szb.cnssiot.cn/content/{date}/{aid}.html",source))
    for ep in ENDPOINT_RE.findall(text): rows.append(("endpoint",urllib.parse.urljoin(ORIGIN,ep),source))
    for m in MOBILE_RE.findall(text): rows.append(("mobile_asset_fragment",m,source))
    return rows

def cdx(target):
    params={"url":target,"output":"json","fl":"timestamp,original,statuscode,mimetype,digest,length","collapse":"urlkey"}
    url="https://web.archive.org/cdx/search/cdx?"+urllib.parse.urlencode(params)
    try:
        raw,_,_=get(url,"application/json"); data=json.loads(raw.decode("utf-8","replace"))
        if not data: return []
        hdr=data[0]; return [dict(zip(hdr,x)) for x in data[1:]]
    except Exception as e: return [{"error":f"{type(e).__name__}: {e}","original":target}]

def main():
    discovered=[]; resource_results=[]; probe_results=[]; errors=[]
    try:
        raw,final,h=get(SNAP,"text/html,*/*"); text=decode(raw,h); parser=P(); parser.feed(text)
        discovered += extract(text,"root_snapshot")
        # Record all explicit HTML media/scripts/links from the seed.
        for u in parser.images: discovered.append(("html_img",normalize_origin(ORIGIN,u),"root_snapshot"))
        for u in parser.links:
            absu=normalize_origin(ORIGIN,u)
            if "szb.cnssiot.cn" in absu: discovered.append(("html_link",absu,"root_snapshot"))
        scripts=[]
        for src in parser.scripts:
            original=normalize_origin(ORIGIN,src); scripts.append(original); discovered.append(("script",original,"root_snapshot"))
        # Fetch historical scripts through the same Wayback timestamp and extract CMS path knowledge.
        def script_one(original):
            try:
                r,f,hh=get(archive_for_original(original)); t=decode(r,hh)
                return original,extract(t,original),t[:20000],""
            except Exception as e: return original,[],"",f"{type(e).__name__}: {e}"
        with ThreadPoolExecutor(max_workers=10) as pool:
            fs=[pool.submit(script_one,s) for s in scripts]
            for fut in as_completed(fs):
                original,rows,snippet,err=fut.result(); discovered+=rows
                resource_results.append({"resource":original,"archive_url":archive_for_original(original),"kind":"script","extract_count":len(rows),"text_snippet":snippet[:3000],"error":err})
    except Exception as e: errors.append({"stage":"root_snapshot","error":f"{type(e).__name__}: {e}"})

    # Very targeted CDX queries; include non-200 entries because Wayback can have assets under redirects/revisits.
    targets=[
        "szb.cnssiot.cn/Img/*","szb.cnssiot.cn/img/*","szb.cnssiot.cn/content/*",
        "szb.cnssiot.cn/api/*","szb.cnssiot.cn/jquery/*","szb.cnssiot.cn/js/*",
        "szb.cnssiot.cn/css/*",
    ]
    cdx_rows=[]
    with ThreadPoolExecutor(max_workers=7) as pool:
        fs={pool.submit(cdx,t):t for t in targets}
        for fut in as_completed(fs):
            for r in fut.result():
                r["query_target"]=fs[fut]; cdx_rows.append(r)

    # Probe known/derived original URLs now; a resurrected CDN/origin would be valuable even if indexers miss it.
    known=[
        "http://szb.cnssiot.cn/api/Pagelist/48",
        "http://szb.cnssiot.cn/jquery/dayslist",
        "http://szb.cnssiot.cn/content/2021-12/25/edition326_A1.html",
        "http://szb.cnssiot.cn/content/2021-12/25/edition333_A2.html",
        "http://szb.cnssiot.cn/content/2021-12/25/edition327_A3.html",
        "http://szb.cnssiot.cn/content/2021-12/25/edition328_A4.html",
        "http://szb.cnssiot.cn/content/2021-12/25/edition329_A5.html",
        "http://szb.cnssiot.cn/content/2021-12/25/edition330_A6.html",
        "http://szb.cnssiot.cn/content/2021-12/25/edition331_A7.html",
        "http://szb.cnssiot.cn/content/2021-12/25/edition332_A8.html",
    ]
    # Add discovered absolute image/edition/endpoint URLs.
    for kind,val,src in discovered:
        if kind in {"image","html_img","edition_route","endpoint"}:
            if val.startswith("/"): val=urllib.parse.urljoin(ORIGIN,val)
            if val.startswith("http"): known.append(val)
    known=list(dict.fromkeys(known))[:200]
    def probe(url):
        out={"url":url,"ok":"","resolved_url":"","content_type":"","length":"","error":""}
        for candidate in (url,url.replace("http://","https://",1) if url.startswith("http://") else url):
            try:
                r,f,h=get(candidate); out.update({"ok":"yes","resolved_url":f,"content_type":h.get("content-type",""),"length":str(len(r))}); return out
            except Exception as e: out["error"]=f"{type(e).__name__}: {e}"
        return out
    with ThreadPoolExecutor(max_workers=16) as pool:
        fs=[pool.submit(probe,u) for u in known]
        for fut in as_completed(fs): probe_results.append(fut.result())

    # Normalize discovered rows.
    drows=[]; seen=set()
    for kind,val,src in discovered:
        if kind=="image" and not val.startswith("http"): val=urllib.parse.urljoin(ORIGIN,val)
        key=(kind,val,src)
        if key in seen: continue
        seen.add(key); drows.append({"kind":kind,"value":val,"source":src})
    drows.sort(key=lambda r:(r["kind"],r["value"]))
    cdx_rows.sort(key=lambda r:(r.get("query_target",""),r.get("original",""),r.get("timestamp","")))
    probe_results.sort(key=lambda r:(r["ok"]!="yes",r["url"]))

    with (OUT/"discovered_strings.csv").open("w",newline="",encoding="utf-8") as f:
        w=csv.DictWriter(f,fieldnames=["kind","value","source"]); w.writeheader(); w.writerows(drows)
    cfields=["query_target","timestamp","original","statuscode","mimetype","digest","length","error"]
    with (OUT/"cdx_rows.csv").open("w",newline="",encoding="utf-8") as f:
        w=csv.DictWriter(f,fieldnames=cfields,extrasaction="ignore"); w.writeheader(); w.writerows(cdx_rows)
    with (OUT/"resource_extracts.csv").open("w",newline="",encoding="utf-8") as f:
        w=csv.DictWriter(f,fieldnames=["resource","archive_url","kind","extract_count","text_snippet","error"]); w.writeheader(); w.writerows(resource_results)
    with (OUT/"live_probes.csv").open("w",newline="",encoding="utf-8") as f:
        w=csv.DictWriter(f,fieldnames=["url","ok","resolved_url","content_type","length","error"]); w.writeheader(); w.writerows(probe_results)
    report={"discovered_strings":len(drows),"cdx_rows":len(cdx_rows),"scripts_inspected":len(resource_results),"live_probes":len(probe_results),"live_hits":sum(1 for r in probe_results if r["ok"]=="yes"),"cdx_image_rows":sum(1 for r in cdx_rows if str(r.get("mimetype","")).startswith("image/")),"errors":errors,"notes":["Archive timestamps are not publication dates.","Routes/assets must be tied to a specific newspaper date/page before promotion."]}
    (OUT/"report.json").write_text(json.dumps(report,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    print(json.dumps(report,ensure_ascii=False,indent=2),flush=True)
    return 0

if __name__=="__main__": raise SystemExit(main())
