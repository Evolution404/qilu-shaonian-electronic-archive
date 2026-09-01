#!/usr/bin/env python3
"""Exact Common Crawl/WARC recovery for 2012 winter combined-issue Sina posts.

The current desktop/mobile Sina renderings have no media keys for the four 2011-12-31
editor-story posts, and Wayback CDX is unreliable. This pass queries only exact canonical
HTTP/HTTPS post URLs in Common Crawl collections from 2011-2013, retrieves matching WARC
records, extracts historical Sina image/photo URLs, and probes deterministic live CDN variants.
Only provenance, URLs, hashes and dimensions are committed.
"""
from __future__ import annotations

import csv, gzip, hashlib, io, json, re, time, urllib.parse, urllib.request
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed
from PIL import Image

ROOT=Path(__file__).resolve().parents[1]
OUTDIR=ROOT/'data'/'repost_fullpage'/'sina_2012_winter_combined'/'commoncrawl'; OUTDIR.mkdir(parents=True,exist_ok=True)
INDEX_ROWS=OUTDIR/'index_rows.csv'; WARC_ROWS=OUTDIR/'warc_rows.csv'; MEDIA=OUTDIR/'media.csv'; REPORT=OUTDIR/'report.json'
UA='qilu-shaonian-sina-winter-commoncrawl/1.0 (+https://github.com/Evolution404/qilu-shaonian-electronic-archive)'
TIMEOUT=35; MAX_RANGE=20*1024*1024; MAX_BODY=15*1024*1024
PLACEHOLDER='d2b5a30568572332968808f1fd3d0218cd8a8ca41889627168fc6d9ca487e766'
IDS=['blog_4c4fc7d9010116nf','blog_4c4fc7d9010116ni','blog_4c4fc7d9010116nl','blog_4c4fc7d9010116nn','blog_4c4fc7d901011cp9']
TITLE={
IDS[0]:'2012年寒假合刊编辑部的故事之三',IDS[1]:'2012年寒假合刊编辑部的故事之四和六',IDS[2]:'2012年寒假合刊编辑部的故事之五',IDS[3]:'2012年寒假合刊编辑部的故事之七',IDS[4]:'今天我评报'}
MEDIA_RE=re.compile(r'''(?i)(?:https?:)?//(?:s\d+|ss\d+|photo|album|ww\d+|wx\d+)\.(?:sinaimg\.cn|sina\.com\.cn)/[^"'<>\s\\]+''')
PATH_CLASS=re.compile(r'/(middle|bmiddle|large|orignal|thumbnail|mw\d+|orj\d+|square)/',re.I)
HOST_RE=re.compile(r'^(ss?|SS?)(\d+)\.sinaimg\.cn$',re.I)

def get(url,headers=None,limit=None,retries=3):
    last=None
    for a in range(retries):
        try:
            h={'User-Agent':UA,'Accept':'*/*'}; h.update(headers or {})
            req=urllib.request.Request(url,headers=h)
            with urllib.request.urlopen(req,timeout=TIMEOUT) as r:
                b=r.read((limit+1) if limit else -1)
                if limit and len(b)>limit:raise ValueError('response too large')
                return b,r.geturl(),{k.lower():v for k,v in r.headers.items()}
        except Exception as e:last=e; time.sleep(a+1)
    raise last

def indexes():
    b,_,_=get('https://index.commoncrawl.org/collinfo.json',limit=5*1024*1024)
    items=json.loads(b.decode('utf-8','replace'))
    out=[]
    for x in items:
        m=re.search(r'CC-MAIN-(20\d{2})',x.get('id',''))
        if m and 2011<=int(m.group(1))<=2013 and x.get('cdx-api'):out.append(x)
    # If no 2011/2012 collections are exposed, keep all 2013 collections as the closest post-publication captures.
    out.sort(key=lambda x:x['id'])
    return out

def targets():
    out=[]
    for pid in IDS:
        for scheme in ('http','https'):
            out.append({'post_id':pid,'post_title':TITLE[pid],'url':f'{scheme}://blog.sina.com.cn/s/{pid}.html'})
    return out

def query(index,target):
    params={'url':target['url'],'output':'json','filter':'status:200','matchType':'exact'}
    url=index['cdx-api']+'?'+urllib.parse.urlencode(params)
    rows=[]
    try:
        b,_,_=get(url,headers={'Accept':'application/json,text/plain,*/*'},limit=5*1024*1024,retries=2)
        for line in b.decode('utf-8','replace').splitlines():
            try:d=json.loads(line)
            except:continue
            if not isinstance(d,dict):continue
            rows.append({'post_id':target['post_id'],'post_title':target['post_title'],'query_url':target['url'],'index':index['id'],'timestamp':d.get('timestamp',''),'url':d.get('url',''),'status':d.get('status',''),'mime':d.get('mime',d.get('mime-detected','')),'digest':d.get('digest',''),'filename':d.get('filename',''),'offset':d.get('offset',''),'length':d.get('length','')})
        return rows,''
    except Exception as e:return [],f'{type(e).__name__}: {e}'

def parse_headers(block):
    lines=block.decode('latin1','replace').split('\r\n'); h={}
    for line in lines[1:]:
        if ':' in line:
            k,v=line.split(':',1);h[k.strip().lower()]=v.strip()
    return (lines[0] if lines else ''),h

def warc_body(row):
    off=int(row['offset']); ln=int(row['length'])
    if ln<=0 or ln>MAX_RANGE:raise ValueError(f'invalid WARC range {ln}')
    url='https://data.commoncrawl.org/'+row['filename']
    b,_,_=get(url,headers={'Range':f'bytes={off}-{off+ln-1}'},limit=MAX_RANGE,retries=3)
    try:raw=gzip.decompress(b)
    except:raw=gzip.GzipFile(fileobj=io.BytesIO(b)).read(MAX_BODY+3*1024*1024)
    p=raw.find(b'\r\n\r\n'); payload=raw[p+4:] if p>=0 else raw
    q=payload.find(b'\r\n\r\n')
    if q>=0:
        status,h=parse_headers(payload[:q]); body=payload[q+4:]
    else:status='';h={};body=payload
    enc=h.get('content-encoding','').lower()
    if enc=='gzip':
        try:body=gzip.decompress(body)
        except:pass
    return status,h,body[:MAX_BODY]

def extract(text):
    text=text.replace('\\/','/').replace('\\u0026','&')
    return sorted(set(x.strip('"\'()[]{};,') for x in MEDIA_RE.findall(text)))

def variants(url):
    p=urllib.parse.urlsplit(url); host=(p.hostname or '').lower(); m=PATH_CLASS.search(p.path)
    if 'sinaimg.cn' not in host or not m:return [('as_found',url)]
    tail=p.path[m.end():]
    if len(tail)<8:return []
    hosts=[p.netloc]; hm=HOST_RE.match(host)
    if hm:
        n=hm.group(2); hosts.append(('s' if host.startswith('ss') else 'ss')+n+'.sinaimg.cn')
    out=[]
    for netloc in dict.fromkeys(hosts):
        for scheme in ('https','http'):
            for cls in dict.fromkeys([m.group(1).lower(),'middle','bmiddle','large','orignal']):
                path=p.path[:m.start()]+f'/{cls}/'+p.path[m.end():]
                out.append((f'{netloc}:{cls}:{scheme}',urllib.parse.urlunsplit((scheme,netloc,path,p.query,p.fragment))))
    return list(dict.fromkeys(out))

def inspect(src,vname,url):
    r={**src,'variant':vname,'candidate_url':url,'resolved_url':'','http_status':'','bytes':'','sha256':'','width':'','height':'','image_format':'','is_placeholder':'','likely_document':'','error':''}
    try:
        b,final,_=get(url,headers={'Referer':src['post_url'],'Accept':'image/*,*/*;q=0.6'},limit=25*1024*1024,retries=2)
        with Image.open(io.BytesIO(b)) as im:w,h=im.size;fmt=im.format or ''
        sha=hashlib.sha256(b).hexdigest();ph=sha==PLACEHOLDER or 'default_s_' in final or (w==360 and h==360 and fmt.upper()=='GIF');ratio=h/w if w else 0
        r.update({'resolved_url':final,'http_status':'200','bytes':str(len(b)),'sha256':sha,'width':str(w),'height':str(h),'image_format':fmt,'is_placeholder':'yes' if ph else 'no','likely_document':'yes' if (not ph and w>=500 and h>=650 and ratio>=1.12) else 'no'})
    except Exception as e:r['error']=f'{type(e).__name__}: {e}'[:800]
    return r

def main():
    idx=indexes(); tg=targets(); print('indexes', [x['id'] for x in idx],flush=True)
    rows=[];errs=[]
    with ThreadPoolExecutor(max_workers=6) as pool:
        fut={pool.submit(query,i,t):(i['id'],t['url']) for i in idx for t in tg}
        for f in as_completed(fut):
            rr,e=f.result();rows.extend(rr)
            if e:errs.append({'index':fut[f][0],'url':fut[f][1],'error':e})
            if rr:print('INDEX HIT',fut[f],len(rr),flush=True)
    uniq={}
    for r in rows:uniq[(r['filename'],r['offset'],r['digest'])]=r
    rows=list(uniq.values())
    wr=[];sources=[]
    for r in rows:
        x={**r,'warc_recovered':'no','body_bytes':'','media_refs':'0','error':''}
        try:
            _,h,b=warc_body(r); text=b.decode('utf-8','replace') if b else ''; media=extract(text)
            x.update({'warc_recovered':'yes','body_bytes':str(len(b)),'media_refs':str(len(media))})
            for u in media:sources.append({'post_id':r['post_id'],'post_title':r['post_title'],'post_url':r['query_url'],'index':r['index'],'timestamp':r['timestamp'],'source_media_url':u})
        except Exception as e:x['error']=f'{type(e).__name__}: {e}'[:900]
        wr.append(x)
    srcuniq={r['source_media_url']:r for r in sources}
    media_rows=[]
    for src in srcuniq.values():
        for vn,u in variants(src['source_media_url']):media_rows.append(inspect(src,vn,u))
    idxfields=['post_id','post_title','query_url','index','timestamp','url','status','mime','digest','filename','offset','length']
    with INDEX_ROWS.open('w',newline='',encoding='utf-8') as f:w=csv.DictWriter(f,fieldnames=idxfields);w.writeheader();w.writerows(rows)
    wfields=idxfields+['warc_recovered','body_bytes','media_refs','error']
    with WARC_ROWS.open('w',newline='',encoding='utf-8') as f:w=csv.DictWriter(f,fieldnames=wfields,extrasaction='ignore');w.writeheader();w.writerows(wr)
    mfields=['post_id','post_title','post_url','index','timestamp','source_media_url','variant','candidate_url','resolved_url','http_status','bytes','sha256','width','height','image_format','is_placeholder','likely_document','error']
    with MEDIA.open('w',newline='',encoding='utf-8') as f:w=csv.DictWriter(f,fieldnames=mfields);w.writeheader();w.writerows(media_rows)
    dec=set(IDS[:4]); report={'indexes_selected':len(idx),'index_queries':len(idx)*len(tg),'index_rows':len(rows),'index_errors':len(errs),'warc_records_recovered':sum(r['warc_recovered']=='yes' for r in wr),'historical_media_urls':len(srcuniq),'dec31_historical_media_urls':len({r['source_media_url'] for r in sources if r['post_id'] in dec}),'reachable_non_placeholder':sum(r['http_status']=='200' and r['is_placeholder']=='no' for r in media_rows),'dec31_likely_document':sum(r['post_id'] in dec and r['likely_document']=='yes' for r in media_rows),'errors':errs[:50],'notes':['Exact URL Common Crawl recovery only; no broad crawling.','No newspaper/image bytes or WARC bodies are committed.']}
    REPORT.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8'); print(json.dumps(report,ensure_ascii=False,indent=2),flush=True)
if __name__=='__main__':main()
