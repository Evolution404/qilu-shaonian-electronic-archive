#!/usr/bin/env python3
"""Inspect every archived 2004-2005 qlsn.com image and list contemporaneous HTML routes.

This pass is inventory-driven and does not require a surviving parent-page reference.
Image bytes are transient; only dimensions/hashes/OCR metadata are committed.
"""
from __future__ import annotations

import csv
import hashlib
import io
import json
import re
import subprocess
import tempfile
import urllib.request
from pathlib import Path
from PIL import Image

ROOT=Path(__file__).resolve().parents[1]
SRC=ROOT/"data"/"archive_crawl"/"wayback_urls.csv"
OUT=ROOT/"data"/"legacy_2004_2005_assets"
OUT.mkdir(parents=True,exist_ok=True)
UA="qilu-shaonian-electronic-archive/2004-2005-assets-1.0"


def fetch(url):
    req=urllib.request.Request(url,headers={"User-Agent":UA,"Accept":"image/*,*/*"})
    with urllib.request.urlopen(req,timeout=22) as r:
        return r.read(15*1024*1024),r.geturl(),r.headers.get_content_type()

def ocr(img):
    with tempfile.NamedTemporaryFile(suffix=".png") as f:
        im=img.convert("RGB")
        if max(im.size)>3000:
            s=3000/max(im.size); im=im.resize((max(1,int(im.width*s)),max(1,int(im.height*s))))
        im.save(f.name)
        p=subprocess.run(["tesseract",f.name,"stdout","-l","chi_sim+eng","--psm","6"],stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,text=True,timeout=45)
        return re.sub(r"\s+"," ",p.stdout).strip()

def main():
    inv=list(csv.DictReader(SRC.open(newline="",encoding="utf-8")))
    imgs=[]; html=[]
    seen=set()
    for r in inv:
        original=r.get("original",""); ts=r.get("timestamp",""); mt=(r.get("mimetype") or "").lower()
        if not ts.startswith(("2004","2005")):continue
        if "qlsn.com" not in original.lower() or r.get("statuscode")!="200":continue
        if "image/" in mt:
            k=(original,r.get("digest", ""))
            if k not in seen: seen.add(k); imgs.append(r)
        elif "html" in mt:
            html.append({"timestamp":ts,"original":original,"archive_url":r.get("archive_url", ""),"digest":r.get("digest", "")})
    out=[]
    for i,r in enumerate(imgs,1):
        rec={"timestamp":r.get("timestamp", ""),"original":r.get("original", ""),"archive_url":r.get("archive_url", ""),"inventory_digest":r.get("digest", ""),"resolved_url":"","content_type":"","bytes":"","sha256":"","width":"","height":"","format":"","large":"no","document_geometry":"no","ocr_hits":"","ocr_excerpt":"","error":""}
        try:
            data,res,ctype=fetch(rec["archive_url"]); rec["resolved_url"]=res;rec["content_type"]=ctype;rec["bytes"]=str(len(data));rec["sha256"]=hashlib.sha256(data).hexdigest()
            img=Image.open(io.BytesIO(data));img.load();rec["width"]=str(img.width);rec["height"]=str(img.height);rec["format"]=img.format or ""
            aspect=img.width/img.height if img.height else 0
            large=img.width>=500 and img.height>=500 and len(data)>=30000
            doc=img.height>=700 and 0.4<=aspect<=0.9
            rec["large"]="yes" if large else "no";rec["document_geometry"]="yes" if doc else "no"
            if large or doc or len(data)>=70000:
                text=ocr(img);hits=[k for k in ("齐鲁少年","少年报","第","期","版","少先队","记者","山东","2004","2005") if k in text]
                rec["ocr_hits"]="|".join(hits);rec["ocr_excerpt"]=text[:1200]
        except Exception as exc:rec["error"]=f"{type(exc).__name__}: {exc}"
        out.append(rec);print(f"{i}/{len(imgs)} {rec['original']} {rec['width']}x{rec['height']} {rec['ocr_hits']} {rec['error'][:50]}",flush=True)
    fields=["timestamp","original","archive_url","inventory_digest","resolved_url","content_type","bytes","sha256","width","height","format","large","document_geometry","ocr_hits","ocr_excerpt","error"]
    with (OUT/"images.csv").open("w",newline="",encoding="utf-8") as f:w=csv.DictWriter(f,fieldnames=fields);w.writeheader();w.writerows(out)
    with (OUT/"html_routes.csv").open("w",newline="",encoding="utf-8") as f:w=csv.DictWriter(f,fieldnames=["timestamp","original","archive_url","digest"]);w.writeheader();w.writerows(sorted(html,key=lambda r:(r["timestamp"],r["original"])))
    candidates=[r for r in out if r["large"]=="yes" or r["document_geometry"]=="yes" or r["ocr_hits"]]
    report={"archived_image_states":len(imgs),"image_fetch_ok":sum(not r["error"] for r in out),"html_routes":len(html),"large_images":sum(r["large"]=="yes" for r in out),"document_geometry":sum(r["document_geometry"]=="yes" for r in out),"ocr_keyword_rows":sum(bool(r["ocr_hits"]) for r in out),"candidate_rows":len(candidates),"candidates":[{k:r[k] for k in ("timestamp","original","archive_url","bytes","sha256","width","height","ocr_hits","ocr_excerpt")} for r in candidates[:50]],"notes":["Selection is based on archive capture year 2004-2005, not inferred newspaper publication date.","No image bytes are committed; OCR is triage evidence only."]}
    (OUT/"report.json").write_text(json.dumps(report,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    print(json.dumps(report,ensure_ascii=False,indent=2),flush=True);return 0
if __name__=="__main__":raise SystemExit(main())
