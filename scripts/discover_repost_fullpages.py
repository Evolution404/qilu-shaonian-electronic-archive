#!/usr/bin/env python3
"""Scan historical editor/reader archives for original newspaper page images.

Sources:
- Sina blog of 《齐鲁少年》 editorial staff (user 1280296921 / blog id 4c4fc7d9)
- qlsnreadship.wordpress.com reader archive

The script stores URLs and technical metadata only. Image bytes are fetched transiently
for dimension/hash checks and are never committed.
"""
from __future__ import annotations

import csv
import hashlib
import html
import io
import json
import re
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from html.parser import HTMLParser
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
OUTDIR = ROOT / "data" / "repost_fullpage"
OUTDIR.mkdir(parents=True, exist_ok=True)
UA = "qilu-shaonian-repost-fullpage/1.0 (+https://github.com/Evolution404/qilu-shaonian-electronic-archive)"
TIMEOUT = 20
MAX_BYTES = 20 * 1024 * 1024
WORKERS = 16

SINA_LISTS = [f"https://blog.sina.com.cn/s/articlelist_1280296921_0_{i}.html" for i in range(1, 65)]
WP_API = "https://public-api.wordpress.com/rest/v1.1/sites/qlsnreadship.wordpress.com/posts/"

BLOG_RE = re.compile(r"https?://blog\.sina\.com\.cn/s/blog_4c4fc7d9[0-9a-f]+\.html", re.I)
IMG_EXT = re.compile(r"\.(?:jpe?g|png|gif|webp)(?:$|\?)", re.I)
ISSUE_TEXT = re.compile(r"(?:第\s*([0-9０-９]{2,5})\s*期|([0-9０-９]{2,5})\s*期)")
PAGE_TEXT = re.compile(r"(?:第\s*[A-DＡ-Ｄ0-9０-９]{1,3}\s*版|[A-DＡ-Ｄ][0-9０-９]?\s*版)")


class Parser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.links=[]; self.images=[]; self.text=[]
    def handle_starttag(self, tag, attrs):
        d={k.lower():(v or "") for k,v in attrs}; tag=tag.lower()
        if tag=="a" and d.get("href"): self.links.append(d["href"])
        if tag in {"img","source"}:
            for key in ("src","data-src","data-original","data-lazy-src","data-actualsrc"):
                if d.get(key): self.images.append(d[key])
            if d.get("srcset"):
                for part in d["srcset"].split(","):
                    if part.strip(): self.images.append(part.strip().split()[0])
        if tag=="meta":
            k=(d.get("property") or d.get("name") or "").lower()
            if k in {"og:image","twitter:image","twitter:image:src"} and d.get("content"):
                self.images.append(d["content"])
    def handle_data(self, data):
        s=" ".join(data.split())
        if s: self.text.append(s)


def req(url, max_bytes=None):
    r=urllib.request.Request(url,headers={"User-Agent":UA,"Accept":"*/*"})
    with urllib.request.urlopen(r,timeout=TIMEOUT) as x:
        data=x.read() if max_bytes is None else x.read(max_bytes+1)
        if max_bytes is not None and len(data)>max_bytes: raise ValueError("too large")
        return data,x.geturl(),{k.lower():v for k,v in x.headers.items()}


def decode(raw, ctype=""):
    m=re.search(r"charset=([\w.-]+)",ctype,re.I)
    encs=[m.group(1)] if m else []
    encs += ["utf-8","gb18030"]
    best=None
    for enc in encs:
        try:
            t=raw.decode(enc,"replace"); score=t.count("\ufffd")
            if best is None or score<best[0]: best=(score,t)
        except Exception: pass
    return best[1] if best else raw.decode("utf-8","replace")


def parse_html(url):
    raw,final,h=req(url); text=decode(raw,h.get("content-type","")); p=Parser(); p.feed(text)
    visible=html.unescape(" ".join(p.text))
    return final,p,visible


def discover_sina_posts():
    posts={}; errors=[]
    def one(url):
        try:
            final,p,text=parse_html(url)
            found=set(BLOG_RE.findall(" ".join(p.links)))
            # Some pages contain escaped absolute post URLs in scripts/text.
            found.update(BLOG_RE.findall(decode(req(url)[0])))
            return found,None
        except Exception as e: return set(),f"{type(e).__name__}: {e}"
    with ThreadPoolExecutor(max_workers=10) as pool:
        fs={pool.submit(one,u):u for u in SINA_LISTS}
        for fut in as_completed(fs):
            found,err=fut.result(); posts.update({u:"sina_editor_blog" for u in found})
            if err: errors.append({"source":"sina_list","url":fs[fut],"error":err})
    return posts,errors


def discover_wp_posts():
    posts={}; errors=[]
    # WordPress.com API supports up to 100 posts per call and a page_handle cursor.
    url=WP_API+"?number=100"
    for _ in range(20):
        try:
            raw,_,_=req(url); data=json.loads(raw.decode("utf-8","replace"))
            for post in data.get("posts",[]):
                u=post.get("URL") or post.get("url")
                if u: posts[u]="wordpress_reader_archive"
            meta=data.get("meta",{}) or {}
            handle=meta.get("next_page") or meta.get("next_page_handle")
            if not handle: break
            url=WP_API+"?number=100&page_handle="+urllib.parse.quote(str(handle))
        except Exception as e:
            errors.append({"source":"wordpress_api","url":url,"error":f"{type(e).__name__}: {e}"}); break
    # Always include known archive pages even if API changes.
    posts["https://qlsnreadship.wordpress.com/%E5%B0%8F%E8%AF%BB%E8%80%85%E7%BE%A4-%E5%8D%81%E5%91%A8%E5%B9%B4/"]="wordpress_reader_archive"
    return posts,errors


def normalize_media(base, raw):
    v=html.unescape(raw.strip())
    if v.startswith("//"): v="https:"+v
    u=urllib.parse.urljoin(base,v)
    if not u.startswith(("http://","https://")): return ""
    # Prefer original WordPress media instead of resized i0.wp.com proxies.
    p=urllib.parse.urlparse(u)
    if p.hostname and p.hostname.endswith("wp.com") and "/wp-content/uploads/" in p.path:
        if p.hostname.startswith(("i0.","i1.","i2.")):
            # Keep proxy; original is often represented elsewhere too.
            pass
    return u


def scan_post(item):
    url,source=item
    rows=[]
    try:
        final,p,text=parse_html(url)
        issues=[]
        for a,b in ISSUE_TEXT.findall(text): issues.append(a or b)
        issues=list(dict.fromkeys(issues))[:20]
        page_hint="yes" if PAGE_TEXT.search(text) else ""
        seen=set()
        for raw in p.images:
            media=normalize_media(final,raw)
            if not media or media in seen: continue
            seen.add(media)
            rows.append({"source":source,"post_url":url,"resolved_post_url":final,"issue_hints":"|".join(issues),"page_text_hint":page_hint,"media_url":media})
        return rows,None
    except Exception as e:
        return [],{"source":source,"url":url,"error":f"{type(e).__name__}: {e}"}


def inspect(row):
    out=dict(row); out.update({"resolved_media_url":"","http_status":"","content_type":"","content_length":"","sha256":"","width":"","height":"","image_format":"","portrait_ratio":"","likely_page_scan":"","fetch_error":""})
    try:
        raw,final,h=req(row["media_url"],MAX_BYTES); c=h.get("content-type","").split(";",1)[0]
        out.update({"resolved_media_url":final,"http_status":"200","content_type":c,"content_length":str(len(raw)),"sha256":hashlib.sha256(raw).hexdigest()})
        if c.startswith("image/") or IMG_EXT.search(final):
            with Image.open(io.BytesIO(raw)) as im:
                w,hg=im.size; ratio=hg/w if w else 0
                out.update({"width":str(w),"height":str(hg),"image_format":str(im.format or ""),"portrait_ratio":f"{ratio:.3f}"})
                # Looser than final verification: historical newspaper photos may be cropped.
                if w>=700 and hg>=950 and ratio>=1.10: out["likely_page_scan"]="yes"
                else: out["likely_page_scan"]="no"
    except Exception as e: out["fetch_error"]=f"{type(e).__name__}: {e}"
    return out


def main():
    sina,e1=discover_sina_posts(); wp,e2=discover_wp_posts(); posts={**sina,**wp}; errors=e1+e2
    print(f"posts discovered={len(posts)} sina={len(sina)} wp={len(wp)}",flush=True)
    media=[]
    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        fs={pool.submit(scan_post,it):it for it in posts.items()}
        for fut in as_completed(fs):
            rows,err=fut.result(); media.extend(rows)
            if err: errors.append(err)
    # Dedup same image used by multiple wrappers only within same post/source context.
    uniq={ (r["source"],r["post_url"],r["media_url"]):r for r in media }
    media=list(uniq.values())
    print(f"media refs={len(media)}",flush=True)
    inspected=[]
    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        fs=[pool.submit(inspect,r) for r in media]
        for i,fut in enumerate(as_completed(fs),1):
            r=fut.result(); inspected.append(r)
            if r.get("likely_page_scan")=="yes": print("PAGE-CANDIDATE",r["width"],r["height"],r["post_url"],r["media_url"],flush=True)
    inspected.sort(key=lambda r:(r.get("likely_page_scan")!="yes",r["source"],r["post_url"],r["media_url"]))
    fields=["source","post_url","resolved_post_url","issue_hints","page_text_hint","media_url","resolved_media_url","http_status","content_type","content_length","sha256","width","height","image_format","portrait_ratio","likely_page_scan","fetch_error"]
    with (OUTDIR/"media_candidates.csv").open("w",newline="",encoding="utf-8") as f:
        w=csv.DictWriter(f,fieldnames=fields); w.writeheader(); w.writerows(inspected)
    report={"sina_posts":len(sina),"wordpress_posts":len(wp),"total_posts":len(posts),"media_refs":len(media),"reachable_media":sum(1 for r in inspected if r["http_status"]=="200"),"likely_page_scans":sum(1 for r in inspected if r["likely_page_scan"]=="yes"),"errors":errors[:200],"notes":["Candidates require visual/OCR verification before promotion.","Only metadata is committed; third-party image bytes are transient."]}
    (OUTDIR/"report.json").write_text(json.dumps(report,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    print(json.dumps(report,ensure_ascii=False,indent=2),flush=True)
    return 0

if __name__=="__main__": raise SystemExit(main())
