#!/usr/bin/env python3
"""OCR likely page scans found in historical editor/reader repost archives."""
from __future__ import annotations
import csv, os, re, subprocess, tempfile, urllib.request
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
SRC=ROOT/"data"/"repost_fullpage"/"media_candidates.csv"
OUT=ROOT/"data"/"repost_fullpage"/"ocr_candidates.csv"
UA="qilu-shaonian-repost-ocr/1.0 (+https://github.com/Evolution404/qilu-shaonian-electronic-archive)"
KEYS=["齐鲁少年","齐鲁少年报","第","期","版","少先队","编辑部"]
ISSUE=re.compile(r"第\s*([0-9０-９]{2,5})\s*期")
PAGE=re.compile(r"(?:第\s*([A-DＡ-Ｄ0-9０-９]{1,3})\s*版|([A-DＡ-Ｄ][0-9０-９]?)\s*版)")
def fetch(u):
    r=urllib.request.Request(u,headers={"User-Agent":UA,"Accept":"image/*,*/*"})
    with urllib.request.urlopen(r,timeout=30) as x:return x.read()
def main():
    with SRC.open(newline="",encoding="utf-8") as f: rows=list(csv.DictReader(f))
    # OCR one row per unique original image hash, prefer largest dimensions.
    cand=[r for r in rows if r.get("likely_page_scan")=="yes" and r.get("sha256")]
    by={}
    for r in cand:
        k=r["sha256"]
        area=int(r.get("width") or 0)*int(r.get("height") or 0)
        if k not in by or area>by[k][0]: by[k]=(area,r)
    out=[]
    for _,r in by.values():
        x={"source":r["source"],"post_url":r["post_url"],"media_url":r["media_url"],"sha256":r["sha256"],"width":r["width"],"height":r["height"],"keyword_hits":"","issue_matches":"","page_matches":"","ocr_excerpt":"","classification":"unverified","error":""}
        path=None
        try:
            data=fetch(r["media_url"])
            with tempfile.NamedTemporaryFile(suffix=".jpg",delete=False) as t:t.write(data);path=t.name
            p=subprocess.run(["tesseract",path,"stdout","-l","chi_sim+eng","--psm","6"],stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,timeout=120)
            text=re.sub(r"\s+"," ",p.stdout.replace("\x0c"," ")).strip()
            hits=[k for k in KEYS if k in text]; issues=ISSUE.findall(text); pages=[a or b for a,b in PAGE.findall(text)]
            x.update({"keyword_hits":"|".join(hits),"issue_matches":"|".join(dict.fromkeys(issues)),"page_matches":"|".join(dict.fromkeys(pages)),"ocr_excerpt":text[:2500]})
            if "齐鲁少年" in text and (issues or pages): x["classification"]="strong_newspaper_page_candidate"
            elif "齐鲁少年" in text: x["classification"]="qilu_shaonian_present"
            else: x["classification"]="no_qilu_shaonian_detected"
            if p.returncode!=0:x["error"]=p.stderr[-800:]
        except Exception as e:x["error"]=f"{type(e).__name__}: {e}"
        finally:
            if path and os.path.exists(path):os.unlink(path)
        out.append(x); print(x["classification"],x["keyword_hits"],x["issue_matches"],flush=True)
    fields=["source","post_url","media_url","sha256","width","height","keyword_hits","issue_matches","page_matches","ocr_excerpt","classification","error"]
    with OUT.open("w",newline="",encoding="utf-8") as f:w=csv.DictWriter(f,fieldnames=fields);w.writeheader();w.writerows(out)
    return 0
if __name__=="__main__":raise SystemExit(main())
