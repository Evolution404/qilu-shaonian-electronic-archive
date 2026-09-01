#!/usr/bin/env python3
"""Inventory the Wayback URL surface for szb.cnssiot.cn with one low-load CDX query.

Instead of guessing individual URLs, ask the archive once for distinct successfully captured URLs
from 2021-01-01 through 2023-12-31, then classify the returned inventory for:
- api/Pagelist and other /api/ endpoints;
- /jquery/ dynamic endpoints;
- /Content/themes/mobileslide/ JS/CSS;
- /Img/ media (especially 2021/12 and PDFs);
- content edition/article pages.

For a small high-value subset, retrieve the archived body and extract Pagepic/Pagepdf/media/edition
signals. Only URL inventory, metadata, hashes, and short excerpts are committed; no newspaper/media
bytes or full archived page bodies are committed.
"""
from __future__ import annotations
import csv, hashlib, json, re, time, urllib.parse, urllib.request
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'data'/'szb_wayback_inventory';OUT.mkdir(parents=True,exist_ok=True)
URLS=OUT/'urls.csv';DETAIL=OUT/'high_value_responses.csv';ASSETS=OUT/'asset_refs.csv';REPORT=OUT/'report.json'
UA='qilu-shaonian-wayback-inventory/1.0 (+https://github.com/Evolution404/qilu-shaonian-electronic-archive)'
TIMEOUT=60;MAX=10*1024*1024
CDX='https://web.archive.org/cdx/search/cdx'
FIELD_RE=re.compile(r'(?i)\b(Pagepic|Pagepdf|mobilepath|pcpath|zoompic|Pagelink|Pagename|Pagetitle|editionid|allpagefile)\b')
MEDIA_RE=re.compile(r'''(?i)(?:https?:)?//szb\.cnssiot\.cn/[^"'<>\s\\]+?\.(?:pdf|jpe?g|png)(?:\?[^"'<>\s\\]*)?|/?Img/[^"'<>\s\\]+?\.(?:pdf|jpe?g|png)(?:\?[^"'<>\s\\]*)?''')
EDITION_RE=re.compile(r'(?i)content/(\d{4}-\d{2}/\d{2})/edition(\d+)_([A-Z]\d+)\.html')


def get(url,accept='*/*',limit=MAX,retries=3):
 last=None
 for a in range(retries):
  try:
   req=urllib.request.Request(url,headers={'User-Agent':UA,'Accept':accept})
   with urllib.request.urlopen(req,timeout=TIMEOUT) as r:
    b=r.read(limit+1)
    if len(b)>limit:raise ValueError(f'too large >{limit}')
    return b,r.geturl(),{k.lower():v for k,v in r.headers.items()}
  except Exception as e:last=e;time.sleep(3+a*5)
 raise last

def decode(b,h):
 ct=h.get('content-type','');m=re.search(r'charset=([\w.-]+)',ct,re.I);best=None
 for enc in (([m.group(1)] if m else [])+['utf-8','gb18030','latin1']):
  try:
   t=b.decode(enc,'replace');q=t.count('\ufffd')
   if best is None or q<best[0]:best=(q,t)
  except:pass
 return best[1] if best else b.decode('utf-8','replace')

def classify(u):
 l=u.lower();cats=[]
 if '/api/' in l:cats.append('api')
 if '/api/pagelist/' in l:cats.append('pagelist')
 if '/jquery/' in l:cats.append('jquery')
 if '/content/themes/mobileslide/' in l:cats.append('mobileslide')
 if '/img/' in l:cats.append('img')
 if '/img/2021/12/' in l:cats.append('img_2021_12')
 if re.search(r'\.pdf(?:\?|$)',l):cats.append('pdf')
 if re.search(r'edition\d+_[a-z]\d+\.html',l):cats.append('edition')
 if '/content/2021-12/25/' in l:cats.append('issue_2021_12_25')
 return '|'.join(cats) or 'other'
def high_value(u,cat):
 l=u.lower()
 return ('pagelist' in cat or 'img_2021_12' in cat or 'edition' in cat or 'mobileslide' in cat or '/jquery/readtitle' in l)

def main():
 params={
  'url':'szb.cnssiot.cn/*','from':'2021','to':'2023','output':'json',
  'fl':'timestamp,original,statuscode,mimetype,digest,length','filter':'statuscode:200',
  'collapse':'urlkey','filter':'!mimetype:warc/revisit','limit':'20000'
 }
 api=CDX+'?'+urllib.parse.urlencode(params)
 errors=[];rows=[]
 try:
  b,_,_=get(api,'application/json,text/plain,*/*',25*1024*1024,3);t=b.decode('utf-8','replace')
  try:data=json.loads(t)
  except Exception:
   data=[]
   # tolerate CDX text/JSON-lines variants
   for line in t.splitlines():
    try:data.append(json.loads(line))
    except:pass
  if isinstance(data,list) and data and isinstance(data[0],list):
   hdr=data[0]
   for vals in data[1:]:
    d=dict(zip(hdr,vals));u=d.get('original','');rows.append({'timestamp':d.get('timestamp',''),'original':u,'statuscode':d.get('statuscode',''),'mimetype':d.get('mimetype',''),'digest':d.get('digest',''),'length':d.get('length',''),'category':classify(u)})
  elif isinstance(data,list):
   for d in data:
    if not isinstance(d,dict):continue
    u=d.get('original',d.get('url',''));rows.append({'timestamp':d.get('timestamp',''),'original':u,'statuscode':str(d.get('statuscode',d.get('status',''))),'mimetype':d.get('mimetype',d.get('mime','')),'digest':d.get('digest',''),'length':str(d.get('length','')),'category':classify(u)})
 except Exception as e:errors.append({'stage':'cdx_inventory','api':api,'error':f'{type(e).__name__}: {e}'})
 # Deduplicate again defensively by canonical original URL.
 uniq={}
 for r in rows:uniq[r['original']]=r
 rows=sorted(uniq.values(),key=lambda r:(r['category'],r['original']))
 with URLS.open('w',newline='',encoding='utf-8') as f:
  fs=['timestamp','original','statuscode','mimetype','digest','length','category'];w=csv.DictWriter(f,fieldnames=fs);w.writeheader();w.writerows(rows)
 high=[r for r in rows if high_value(r['original'],r['category'])]
 # Cap body fetches: inventory is authoritative; body checks prioritize target issue/API/scripts.
 pri=sorted(high,key=lambda r:(0 if 'pagelist' in r['category'] else 1 if 'issue_2021_12_25' in r['category'] else 2 if 'img_2021_12' in r['category'] else 3,r['original']))[:160]
 details=[];assetrows=[]
 for i,r in enumerate(pri):
  cap=f'https://web.archive.org/web/{r["timestamp"]}id_/{r["original"]}'
  d={**r,'capture_url':cap,'response_status':'','content_type':'','bytes':'','sha256':'','fields':'','edition_mentions':'','media_refs':'','excerpt':'','error':''}
  try:
   b,final,h=get(cap,'application/json,text/javascript,text/plain,text/html,image/*,application/pdf,*/*',MAX,2);ct=h.get('content-type','').lower();magic='pdf' if b.startswith(b'%PDF-') else 'jpeg' if b[:3]==b'\xff\xd8\xff' else 'png' if b.startswith(b'\x89PNG\r\n\x1a\n') else ''
   if magic:
    d.update({'response_status':'media','content_type':ct,'bytes':str(len(b)),'sha256':hashlib.sha256(b).hexdigest(),'excerpt':magic})
   else:
    text=decode(b,h);fields=sorted(set(FIELD_RE.findall(text)));eds=sorted(set(f'{m.group(3).upper()}:{m.group(2)}@{m.group(1)}' for m in EDITION_RE.finditer(text)));media=sorted(set(urllib.parse.urljoin('http://szb.cnssiot.cn/',x.replace('\\/','/')) for x in MEDIA_RE.findall(text)))
    d.update({'response_status':'text','content_type':ct,'bytes':str(len(b)),'sha256':hashlib.sha256(b).hexdigest(),'fields':'|'.join(fields),'edition_mentions':'|'.join(eds),'media_refs':'|'.join(media[:80]),'excerpt':re.sub(r'\s+',' ',text)[:1800]})
    for u in media:assetrows.append({'source_original':r['original'],'source_timestamp':r['timestamp'],'asset_url':u})
  except Exception as e:d['response_status']='error';d['error']=f'{type(e).__name__}: {e}'[:1200]
  details.append(d)
  if (i+1)%20==0:print('body progress',i+1,'/',len(pri),flush=True)
 with DETAIL.open('w',newline='',encoding='utf-8') as f:
  fs=['timestamp','original','statuscode','mimetype','digest','length','category','capture_url','response_status','content_type','bytes','sha256','fields','edition_mentions','media_refs','excerpt','error'];w=csv.DictWriter(f,fieldnames=fs);w.writeheader();w.writerows(details)
 au={x['asset_url']:x for x in assetrows}
 with ASSETS.open('w',newline='',encoding='utf-8') as f:
  fs=['source_original','source_timestamp','asset_url'];w=csv.DictWriter(f,fieldnames=fs);w.writeheader();w.writerows(sorted(au.values(),key=lambda x:x['asset_url']))
 cats={}
 for r in rows:
  for c in r['category'].split('|'):cats[c]=cats.get(c,0)+1
 report={'cdx_api':api,'inventory_urls':len(rows),'category_counts':cats,'high_value_urls':len(high),'high_value_bodies_checked':len(details),'pagelist_urls':[r['original'] for r in rows if 'pagelist' in r['category']],'issue_2021_12_25_urls':sum('issue_2021_12_25' in r['category'] for r in rows),'img_2021_12_urls':sum('img_2021_12' in r['category'] for r in rows),'pdf_urls':sum('pdf' in r['category'] for r in rows),'recovered_media_bodies':sum(r['response_status']=='media' for r in details),'discovered_asset_refs':len(au),'errors':errors,'notes':['Single low-load domain inventory query replaces many guessed-path queries.','A successful inventory establishes archived URL existence even if replay later fails.','No full archived text or third-party media bytes are committed.']}
 REPORT.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8');print(json.dumps(report,ensure_ascii=False,indent=2),flush=True)
if __name__=='__main__':main()
