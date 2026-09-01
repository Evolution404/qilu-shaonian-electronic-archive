#!/usr/bin/env python3
"""Probe alternate live Sina renderings for deleted 2012-winter media references.

Old Sina blogs historically had desktop, HTTP and mobile dpool renderings. Some legacy
attributes can survive in one rendering after disappearing from another. This probe compares
those renderings for the four Dec-31 combined-issue posts and records legacy image/photo URLs.
No media bytes are committed.
"""
from __future__ import annotations
import csv, html, json, re, urllib.request
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
OUTDIR=ROOT/'data'/'repost_fullpage'/'sina_2012_winter_combined'/'alternate_views'; OUTDIR.mkdir(parents=True,exist_ok=True)
OUT=OUTDIR/'views.csv'; MEDIA=OUTDIR/'media.csv'; REPORT=OUTDIR/'report.json'
UA='Mozilla/5.0 qilu-shaonian-sina-alt-view/1.0'
IDS=['blog_4c4fc7d9010116nf','blog_4c4fc7d9010116ni','blog_4c4fc7d9010116nl','blog_4c4fc7d9010116nn','blog_4c4fc7d901011cp9']
MEDIA_RE=re.compile(r'''(?i)(?:(?:https?:)?(?:\\?/\\?/|//))?(?:s\d+|ss\d+|photo|album|ww\d+|wx\d+)\.(?:sinaimg\.cn|sina\.com\.cn)/[^"'<>\s\\]+''')

def urls(post_id):
    return [
        ('desktop_https',f'https://blog.sina.com.cn/s/{post_id}.html'),
        ('desktop_http',f'http://blog.sina.com.cn/s/{post_id}.html'),
        ('mobile_https',f'https://blog.sina.cn/dpool/blog/s/{post_id}.html'),
        ('mobile_http',f'http://blog.sina.cn/dpool/blog/s/{post_id}.html'),
    ]

def fetch(url):
    req=urllib.request.Request(url,headers={'User-Agent':UA,'Accept':'text/html,*/*;q=0.6'})
    with urllib.request.urlopen(req,timeout=22) as r:
        b=r.read(7*1024*1024); final=r.geturl(); ct=r.headers.get('content-type','')
    best=None
    for enc in ['utf-8','gb18030']:
        t=b.decode(enc,'replace'); score=t.count('\ufffd')
        if best is None or score<best[0]:best=(score,t)
    return b,final,ct,best[1]

def clean(x):
    x=html.unescape(x).replace('\\/','/').replace('\\u0026','&').strip('"\'()[]{};,')
    if x.startswith('//'):x='http:'+x
    if not x.startswith(('http://','https://')):x='http://'+x
    return x

def main():
    view_rows=[]; media_rows=[]
    for pid in IDS:
        for kind,url in urls(pid):
            row={'post_id':pid,'view':kind,'request_url':url,'resolved_url':'','status':'','bytes':'','content_type':'','media_refs':'0','unique_media_refs':'0','error':''}
            try:
                b,final,ct,text=fetch(url)
                found=[]
                for blob in (text,html.unescape(text),html.unescape(text).replace('\\/','/')):
                    found.extend(clean(x) for x in MEDIA_RE.findall(blob))
                uniq=sorted(set(found))
                row.update({'resolved_url':final,'status':'200','bytes':str(len(b)),'content_type':ct,'media_refs':str(len(found)),'unique_media_refs':str(len(uniq))})
                for u in uniq:media_rows.append({'post_id':pid,'view':kind,'source_url':url,'media_url':u})
            except Exception as e:row['error']=f'{type(e).__name__}: {e}'[:900]
            view_rows.append(row); print(pid,kind,row['status'],row['bytes'],row['unique_media_refs'],row['error'],flush=True)
    with OUT.open('w',newline='',encoding='utf-8') as f:w=csv.DictWriter(f,fieldnames=list(view_rows[0]));w.writeheader();w.writerows(view_rows)
    with MEDIA.open('w',newline='',encoding='utf-8') as f:w=csv.DictWriter(f,fieldnames=['post_id','view','source_url','media_url']);w.writeheader();w.writerows(media_rows)
    dec=set(IDS[:4]); dec_media={r['media_url'] for r in media_rows if r['post_id'] in dec}; cover_media={r['media_url'] for r in media_rows if r['post_id']==IDS[4]}
    report={'views_probed':len(view_rows),'views_recovered':sum(r['status']=='200' for r in view_rows),'media_rows':len(media_rows),'unique_media_urls':len({r['media_url'] for r in media_rows}),'dec31_unique_media_urls':len(dec_media),'cover_post_unique_media_urls':len(cover_media),'dec31_media_urls':sorted(dec_media),'notes':['Alternate live view probe only; media URLs require content verification.','No image bytes are committed.']}
    REPORT.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8');print(json.dumps(report,ensure_ascii=False,indent=2),flush=True)
if __name__=='__main__':main()
