#!/usr/bin/env python3
"""Locate the verified Qilu Shaonian Pagelist/48 response across independent web archives.

Evidence source: the verified 2022-09-28 szb.cnssiot.cn root HTML contains
    var allpagefile = webd + "api/Pagelist/48";

The simple Wayback availability check did not recover a response. This script therefore performs
only low-frequency exact-URL queries against:
- Wayback CDX and Timemap/Memento endpoints;
- Common Crawl CDXJ indexes around late 2021 through early 2023 (serial, throttled);
- Arquivo.pt versionHistory.

When an archive capture exposes retrievable bytes, the response is parsed for edition/page/media
metadata and, for Common Crawl, the exact WARC range is fetched. No archived response body or
newspaper/media bytes are committed; only URLs, capture metadata, hashes and short excerpts.
"""
from __future__ import annotations

import csv, gzip, hashlib, io, json, re, time, urllib.parse, urllib.request
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'data'/'szb_pagelist_48_multiarchive'; OUT.mkdir(parents=True,exist_ok=True)
CAP=OUT/'captures.csv'; RESP=OUT/'responses.csv'; PAGES=OUT/'page_signals.csv'; REPORT=OUT/'report.json'
TARGET='http://szb.cnssiot.cn/api/Pagelist/48'
TARGET_HTTPS='https://szb.cnssiot.cn/api/Pagelist/48'
UA='qilu-shaonian-pagelist48-multiarchive/1.0 (+https://github.com/Evolution404/qilu-shaonian-electronic-archive)'
TIMEOUT=45; MAX=12*1024*1024; MAX_WARC=24*1024*1024
ED={'326':'A1','333':'A2','327':'A3','328':'A4','329':'A5','330':'A6','331':'A7','332':'A8'}
FIELD_RE=re.compile(r'(?i)\b(Pagepic|Pagepdf|mobilepath|pcpath|zoompic|Pagelink|Pagename|Pagetitle|editionid|edition_id|numberid|pageid|qiid|Qiid)\b')
EDITION_RE=re.compile(r'(?i)edition(\d+)_([A-Z]\d+)\.html')
MEDIA_RE=re.compile(r'''(?i)(?:https?:)?//[^"'<>\s\\]+\.(?:pdf|jpe?g|png)(?:\?[^"'<>\s\\]*)?|/?(?:Img|img)/[^"'<>\s\\]+?\.(?:pdf|jpe?g|png)(?:\?[^"'<>\s\\]*)?''')


def get(url,accept='*/*',limit=MAX,retries=2,headers=None):
    last=None
    for a in range(retries):
        try:
            h={'User-Agent':UA,'Accept':accept}; h.update(headers or {})
            req=urllib.request.Request(url,headers=h)
            with urllib.request.urlopen(req,timeout=TIMEOUT) as r:
                b=r.read(limit+1)
                if len(b)>limit: raise ValueError(f'response exceeds {limit}')
                return b,r.geturl(),{k.lower():v for k,v in r.headers.items()}
        except Exception as e:
            last=e; time.sleep(2+a*3)
    raise last


def decode(b,h=None):
    ct=(h or {}).get('content-type','');m=re.search(r'charset=([\w.-]+)',ct,re.I);best=None
    for enc in (([m.group(1)] if m else [])+['utf-8','gb18030','latin1']):
        try:
            t=b.decode(enc,'replace');q=t.count('\ufffd')
            if best is None or q<best[0]:best=(q,t)
        except:pass
    return best[1] if best else b.decode('utf-8','replace')


def add_capture(rows, seen, **kw):
    key=(kw.get('archive',''),kw.get('timestamp',''),kw.get('original_url',''),kw.get('capture_url',''),kw.get('filename',''),kw.get('offset',''))
    if key in seen:return
    seen.add(key);rows.append(kw)


def wayback_cdx(captures,seen,errors):
    for target in [TARGET,TARGET_HTTPS]:
        params={'url':target,'output':'json','fl':'timestamp,original,statuscode,mimetype,digest,length','filter':'statuscode:200','collapse':'digest'}
        api='https://web.archive.org/cdx/search/cdx?'+urllib.parse.urlencode(params)
        try:
            b,_,_=get(api,'application/json,*/*;q=0.5',4*1024*1024,2);data=json.loads(b.decode('utf-8','replace'))
            if data and isinstance(data,list):
                hdr=data[0]
                for vals in data[1:]:
                    d=dict(zip(hdr,vals));ts=d.get('timestamp','');orig=d.get('original',target)
                    add_capture(captures,seen,archive='wayback_cdx',timestamp=ts,original_url=orig,status=d.get('statuscode',''),mime=d.get('mimetype',''),digest=d.get('digest',''),length=d.get('length',''),capture_url=(f'https://web.archive.org/web/{ts}id_/{orig}' if ts else ''),filename='',offset='',index='')
            print('WAYBACK CDX',target,'rows',max(0,len(data)-1) if isinstance(data,list) else 0,flush=True)
        except Exception as e:errors.append({'stage':'wayback_cdx','target':target,'error':f'{type(e).__name__}: {e}'})
        time.sleep(2)


def wayback_timemap(captures,seen,errors):
    # Try both JSON timemap and RFC link-format timemap. Availability API can miss endpoint captures.
    for target in [TARGET,TARGET_HTTPS]:
        encoded=urllib.parse.quote(target,safe=':/')
        urls=[
            'https://web.archive.org/web/timemap/json?url='+urllib.parse.quote(target,safe=''),
            'https://web.archive.org/web/timemap/link/'+encoded,
        ]
        for api in urls:
            try:
                b,_,h=get(api,'application/json,application/link-format,text/plain,*/*',5*1024*1024,2);t=decode(b,h)
                # Common timemap JSON is an array; link format includes datetime and memento URLs.
                try:
                    data=json.loads(t)
                except Exception:data=None
                found=0
                if isinstance(data,list):
                    # First row may be header-like; accept nested values containing a 14-digit timestamp and target URL.
                    for item in data:
                        blob=json.dumps(item,ensure_ascii=False) if not isinstance(item,str) else item
                        m=re.search(r'/web/(\d{14})/(https?://[^"\s]+)',blob)
                        if m:
                            ts,orig=m.group(1),m.group(2);add_capture(captures,seen,archive='wayback_timemap',timestamp=ts,original_url=orig,status='',mime='',digest='',length='',capture_url=f'https://web.archive.org/web/{ts}id_/{orig}',filename='',offset='',index='');found+=1
                for m in re.finditer(r'<(https://web\.archive\.org/web/(\d{14})/([^>]+))>[^\n]*rel="memento"',t,re.I):
                    capurl,ts,orig=m.group(1),m.group(2),m.group(3);add_capture(captures,seen,archive='wayback_timemap',timestamp=ts,original_url=orig,status='',mime='',digest='',length='',capture_url=re.sub(r'/web/(\d{14})/',r'/web/\1id_/',capurl,1),filename='',offset='',index='');found+=1
                print('WAYBACK TIMEMAP',api[:75],'hits',found,flush=True)
            except Exception as e:errors.append({'stage':'wayback_timemap','target':target,'api':api,'error':f'{type(e).__name__}: {e}'})
            time.sleep(2)


def cc_indexes(errors):
    try:
        b,_,_=get('https://index.commoncrawl.org/collinfo.json','application/json',5*1024*1024,2);items=json.loads(b.decode('utf-8','replace'))
    except Exception as e:
        errors.append({'stage':'cc_collinfo','error':f'{type(e).__name__}: {e}'});return []
    out=[]
    for x in items:
        ident=x.get('id','');m=re.search(r'CC-MAIN-(20\d{2})-(\d+)',ident)
        if not m or not x.get('cdx-api'):continue
        y,w=int(m.group(1)),int(m.group(2))
        if (y==2021 and w>=43) or y==2022 or (y==2023 and w<=14):out.append(x)
    out.sort(key=lambda x:x['id'])
    return out


def commoncrawl(captures,seen,errors):
    idx=cc_indexes(errors)
    # Exact URL, one request at a time. CC explicitly rate-limits the public CDX endpoint.
    for i,x in enumerate(idx):
        params={'url':TARGET,'output':'json','filter':'status:200','matchType':'exact'}
        api=x['cdx-api']+'?'+urllib.parse.urlencode(params)
        try:
            b,_,_=get(api,'application/json,text/plain,*/*',5*1024*1024,2);n=0
            for line in b.decode('utf-8','replace').splitlines():
                try:d=json.loads(line)
                except:continue
                add_capture(captures,seen,archive='commoncrawl',timestamp=d.get('timestamp',''),original_url=d.get('url',TARGET),status=d.get('status',''),mime=d.get('mime',d.get('mime-detected','')),digest=d.get('digest',''),length=d.get('length',''),capture_url='',filename=d.get('filename',''),offset=d.get('offset',''),index=x['id']);n+=1
            print('CC',x['id'],'rows',n,flush=True)
        except Exception as e:errors.append({'stage':'commoncrawl','index':x['id'],'api':api,'error':f'{type(e).__name__}: {e}'})
        # 4s keeps this run intentionally low-load.
        if i+1<len(idx):time.sleep(4)


def arquivo(captures,seen,errors):
    for target in [TARGET,TARGET_HTTPS]:
        params={'versionHistory':target,'from':'20211201000000','to':'20231231235959','offset':'0','maxItems':'200'}
        api='https://arquivo.pt/textsearch?'+urllib.parse.urlencode(params)
        try:
            b,_,_=get(api,'application/json',8*1024*1024,2);d=json.loads(b.decode('utf-8','replace'));items=d.get('response_items',[]) or []
            for item in items:
                ts=str(item.get('tstamp',''));orig=item.get('originalURL',target);cap=item.get('linkToOriginalFile') or item.get('linkToNoFrame') or item.get('linkToArchive') or ''
                add_capture(captures,seen,archive='arquivo_pt',timestamp=ts,original_url=orig,status=str(item.get('statusCode',item.get('status',''))),mime=item.get('mimeType',''),digest=item.get('digest',''),length=str(item.get('contentLength','')),capture_url=cap,filename='',offset='',index=item.get('collection',''))
            print('ARQUIVO',target,'rows',len(items),flush=True)
        except Exception as e:errors.append({'stage':'arquivo','target':target,'error':f'{type(e).__name__}: {e}'})
        time.sleep(2)


def parse_warc(row):
    off=int(row['offset']);ln=int(row['length'])
    if ln<=0 or ln>MAX_WARC:raise ValueError(f'invalid range {ln}')
    u='https://data.commoncrawl.org/'+row['filename'];b,_,_=get(u,'*/*',MAX_WARC,2,{'Range':f'bytes={off}-{off+ln-1}'})
    try:raw=gzip.decompress(b)
    except:raw=gzip.GzipFile(fileobj=io.BytesIO(b)).read(MAX+3*1024*1024)
    a=raw.find(b'\r\n\r\n');payload=raw[a+4:] if a>=0 else raw
    z=payload.find(b'\r\n\r\n');body=payload[z+4:] if z>=0 else payload
    return body[:MAX]


def response_for_capture(row):
    if row['archive']=='commoncrawl':return parse_warc(row),'commoncrawl_warc',{}
    if not row['capture_url']:raise ValueError('missing capture_url')
    b,final,h=get(row['capture_url'],'application/json,text/javascript,text/plain,text/html,*/*',MAX,2);return b,final,h


def parse_signals(text,source_key):
    fields=sorted(set(FIELD_RE.findall(text)));eds=sorted(set((m.group(2).upper(),m.group(1)) for m in EDITION_RE.finditer(text)))
    media=sorted(set(urllib.parse.urljoin('http://szb.cnssiot.cn/',x.replace('\\/','/')) for x in MEDIA_RE.findall(text)))
    rows=[]
    # Short contexts around known edition IDs and media refs.
    for page,eid in sorted((p,e) for e,p in ED.items()):
        for m in re.finditer(r'(?<!\d)'+re.escape(eid)+r'(?!\d)',text):
            c=re.sub(r'\s+',' ',text[max(0,m.start()-350):min(len(text),m.end()+650)]).strip()[:1100]
            if any(k.lower() in c.lower() for k in ['page','pic','pdf','edition','img']):rows.append({'source_key':source_key,'page':page,'edition_id':eid,'context':c})
            break
    return fields,eds,media,rows


def main():
    captures=[];seen=set();errors=[]
    wayback_cdx(captures,seen,errors);wayback_timemap(captures,seen,errors);commoncrawl(captures,seen,errors);arquivo(captures,seen,errors)
    responses=[];page_rows=[]
    for idx,row in enumerate(captures):
        rr={**row,'response_status':'','resolved_capture':'','response_content_type':'','response_bytes':'','sha256':'','fields':'','edition_mentions':'','media_refs':'','excerpt':'','error':''}
        try:
            b,final,h=response_for_capture(row);t=decode(b,h);fields,eds,media,pr=parse_signals(t,f'{row["archive"]}:{row["timestamp"]}:{idx}')
            rr.update({'response_status':'recovered','resolved_capture':final,'response_content_type':h.get('content-type',''),'response_bytes':str(len(b)),'sha256':hashlib.sha256(b).hexdigest(),'fields':'|'.join(fields),'edition_mentions':'|'.join(f'{p}:{e}' for p,e in eds),'media_refs':'|'.join(media[:80]),'excerpt':re.sub(r'\s+',' ',t)[:1800]});page_rows.extend(pr)
        except Exception as e:rr['response_status']='error';rr['error']=f'{type(e).__name__}: {e}'[:1800]
        responses.append(rr)
    capfields=['archive','timestamp','original_url','status','mime','digest','length','capture_url','filename','offset','index']
    with CAP.open('w',newline='',encoding='utf-8') as f:w=csv.DictWriter(f,fieldnames=capfields,extrasaction='ignore');w.writeheader();w.writerows(captures)
    respfields=capfields+['response_status','resolved_capture','response_content_type','response_bytes','sha256','fields','edition_mentions','media_refs','excerpt','error']
    with RESP.open('w',newline='',encoding='utf-8') as f:w=csv.DictWriter(f,fieldnames=respfields,extrasaction='ignore');w.writeheader();w.writerows(responses)
    with PAGES.open('w',newline='',encoding='utf-8') as f:w=csv.DictWriter(f,fieldnames=['source_key','page','edition_id','context']);w.writeheader();w.writerows(page_rows)
    pages=sorted(set(r['page'] for r in page_rows));media=set()
    for r in responses:
        media.update(x for x in r['media_refs'].split('|') if x)
    report={'target':TARGET,'captures_total':len(captures),'captures_by_archive':{a:sum(r['archive']==a for r in captures) for a in sorted(set(r['archive'] for r in captures))},'responses_recovered':sum(r['response_status']=='recovered' for r in responses),'responses_with_page_fields':sum(bool(r['fields']) for r in responses),'pages_signaled':pages,'unique_media_refs':len(media),'errors':errors[:80],'notes':['All archive queries are exact-URL and low-frequency.','A zero is meaningful only for archive queries that completed successfully; service failures remain errors.','No archived response body or newspaper/media bytes are committed.']}
    REPORT.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8');print(json.dumps(report,ensure_ascii=False,indent=2),flush=True)
if __name__=='__main__':main()
