#!/usr/bin/env python3
"""Inspect editor-blog posts surrounding the 2012 winter combined issue for hidden media.

The visible post crawler recovered only the combined-issue cover.  This targeted pass fetches
the Dec 31 editor-story posts plus the Jan 10 review post and extracts media references from
normal attributes, legacy real_src, escaped fragments, CSS/JS strings, anchors and Sina
photo hosts. It records URL evidence only; no media bytes are committed.
"""
from __future__ import annotations
import csv, html, json, re, urllib.parse, urllib.request
from pathlib import Path
from bs4 import BeautifulSoup

ROOT=Path(__file__).resolve().parents[1]
OUTDIR=ROOT/'data'/'repost_fullpage'/'sina_2012_winter_combined'; OUTDIR.mkdir(parents=True,exist_ok=True)
OUT=OUTDIR/'hidden_media_refs.csv'; REPORT=OUTDIR/'hidden_media_report.json'
UA='Mozilla/5.0 qilu-shaonian-winter-combined-inspector/1.0'
POSTS=[
('2011-12-31','2012年寒假合刊编辑部的故事之三','https://blog.sina.com.cn/s/blog_4c4fc7d9010116nf.html'),
('2011-12-31','2012年寒假合刊编辑部的故事之四和六','https://blog.sina.com.cn/s/blog_4c4fc7d9010116ni.html'),
('2011-12-31','2012年寒假合刊编辑部的故事之五','https://blog.sina.com.cn/s/blog_4c4fc7d9010116nl.html'),
('2011-12-31','2012年寒假合刊编辑部的故事之七','https://blog.sina.com.cn/s/blog_4c4fc7d9010116nn.html'),
('2012-01-10','今天我评报','https://blog.sina.com.cn/s/blog_4c4fc7d901011cp9.html'),
]
URL_RE=re.compile(r'''(?i)(?:https?:)?(?:\\?/\\?/|//)[^"'<>\s]+''')
MEDIA_HINT=re.compile(r'(?i)(sinaimg|sinajs|blogphoto|photo|album|\.jpe?g(?:[?&#]|$)|\.png(?:[?&#]|$)|\.gif(?:[?&#]|$)|\.bmp(?:[?&#]|$)|\.pdf(?:[?&#]|$))')

def get(url):
    req=urllib.request.Request(url,headers={'User-Agent':UA,'Accept':'text/html,*/*;q=0.7'})
    with urllib.request.urlopen(req,timeout=25) as r:
        raw=r.read(8*1024*1024); ctype=r.headers.get('content-type','')
    for enc in ['utf-8','gb18030']:
        try:
            t=raw.decode(enc); return t
        except: pass
    return raw.decode('utf-8','replace')

def clean(u):
    u=html.unescape(u).replace('\\/','/').replace('\\u0026','&').strip('"\'()[]{};,')
    if u.startswith('//'):u='http:'+u
    return u

def main():
    rows=[]; errors=[]
    for date,title,url in POSTS:
        try:text=get(url)
        except Exception as e: errors.append({'post_url':url,'error':f'{type(e).__name__}: {e}'}); continue
        soup=BeautifulSoup(text,'html.parser')
        found=[]
        for tag in soup.find_all(True):
            for attr,val in tag.attrs.items():
                vals=val if isinstance(val,list) else [val]
                for x in vals:
                    if isinstance(x,str) and MEDIA_HINT.search(x):found.append((f'attr:{attr}',x))
        blobs=[text,html.unescape(text),html.unescape(text).replace('\\/','/')]
        for blob in blobs:
            for x in URL_RE.findall(blob):
                if MEDIA_HINT.search(x):found.append(('raw_url',x))
        seen=set()
        for kind,raw in found:
            u=clean(raw)
            if not u.startswith(('http://','https://')):continue
            key=(kind,u)
            if key in seen:continue
            seen.add(key)
            p=urllib.parse.urlsplit(u)
            rows.append({'post_date':date,'post_title':title,'post_url':url,'source_kind':kind,'media_url':u,'host':p.netloc,'path':p.path,'looks_newspaper_asset':'yes' if re.search(r'(?i)(large|orignal|middle|bmiddle|\.pdf|\.jpe?g)',u) else 'no'})
        print(title,'refs',len(seen),flush=True)
    fields=['post_date','post_title','post_url','source_kind','media_url','host','path','looks_newspaper_asset']
    with OUT.open('w',newline='',encoding='utf-8') as f:w=csv.DictWriter(f,fieldnames=fields);w.writeheader();w.writerows(rows)
    report={'posts_targeted':len(POSTS),'posts_recovered':len(POSTS)-len(errors),'media_refs':len(rows),'unique_media_urls':len({r['media_url'] for r in rows}),'by_post':{},'errors':errors,'notes':['Broad hidden-media inspection only; each candidate must be content-verified.','No media bytes are committed.']}
    for _,title,url in POSTS:
        rr=[r for r in rows if r['post_url']==url]; report['by_post'][url]={'title':title,'refs':len(rr),'unique_urls':len({r['media_url'] for r in rr}),'hosts':sorted({r['host'] for r in rr})}
    REPORT.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8'); print(json.dumps(report,ensure_ascii=False,indent=2),flush=True)
if __name__=='__main__':main()
