#!/usr/bin/env python3
"""Recover the verified 2021-12-25 all-page API: /api/Pagelist/48.

The verified 2022-09-28 root HTML contains this exact inline contract:
    var allpagefile = webd + "api/Pagelist/48";
This is therefore a deployment-specific target, not a generic CMS guess.

The script probes exact/closest Wayback captures plus live HTTP/HTTPS, normalizes any JSON/JS
response into page rows, extracts edition ids/routes and all /Img/ or PDF/JPG media references,
and then verifies media URLs by response magic/hash where possible. It commits metadata only.
"""
from __future__ import annotations
import csv, hashlib, json, re, time, urllib.parse, urllib.request
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'data'/'szb_pagelist_48';OUT.mkdir(parents=True,exist_ok=True)
RESP=OUT/'responses.csv';PAGES=OUT/'pages.csv';MEDIA=OUT/'media.csv';REPORT=OUT/'report.json'
ORIGIN='http://szb.cnssiot.cn/'
TARGET='http://szb.cnssiot.cn/api/Pagelist/48'
TS='20220928010853';UA='qilu-shaonian-pagelist48/1.0 (+https://github.com/Evolution404/qilu-shaonian-electronic-archive)'
TIMEOUT=40;MAX=12*1024*1024;MAX_MEDIA=35*1024*1024
KNOWN={'326':'A1','333':'A2','327':'A3','328':'A4','329':'A5','330':'A6','331':'A7','332':'A8'}
URL_RE=re.compile(r'''(?i)(?:https?:)?//[^"'<>\s\\]+|/?(?:Img|img)/[^"'<>\s\\]+''')
EDITION_RE=re.compile(r'(?i)edition(\d+)_([A-Z]\d+)\.html')
MEDIA_RE=re.compile(r'(?i)\.(?:pdf|jpe?g|png)(?:\?|$)')


def get(url,accept='*/*',limit=MAX,retries=3):
 last=None
 for a in range(retries):
  try:
   req=urllib.request.Request(url,headers={'User-Agent':UA,'Accept':accept})
   with urllib.request.urlopen(req,timeout=TIMEOUT) as r:
    b=r.read(limit+1)
    if len(b)>limit:raise ValueError('too large')
    return b,r.geturl(),{k.lower():v for k,v in r.headers.items()}
  except Exception as e:last=e;time.sleep(1+a*2)
 raise last

def archive(u,ts=TS):return f'https://web.archive.org/web/{ts}id_/{u}'
def available(u,day):
 try:
  api='https://archive.org/wayback/available?'+urllib.parse.urlencode({'url':u,'timestamp':day})
  b,_,_=get(api,'application/json',2*1024*1024,2);d=json.loads(b.decode('utf-8','replace'));c=(d.get('archived_snapshots') or {}).get('closest') or {}
  if c.get('available') and c.get('url'):return re.sub(r'/web/(\d+)/',r'/web/\1id_/',c['url'],count=1),c.get('timestamp','')
 except:pass
 return '',''
def textdecode(b,h):
 ct=h.get('content-type','');m=re.search(r'charset=([\w.-]+)',ct,re.I);best=None
 for enc in (([m.group(1)] if m else [])+['utf-8','gb18030']):
  try:
   t=b.decode(enc,'replace');q=t.count('\ufffd')
   if best is None or q<best[0]:best=(q,t)
  except:pass
 return best[1] if best else b.decode('utf-8','replace')
def absurl(x):
 x=x.strip().strip('"\'').replace('\\/','/')
 if x.startswith('//'):x='http:'+x
 return urllib.parse.urljoin(ORIGIN,x)

def attempts():
 out=[('wayback_exact',archive(TARGET))]
 for day in ['20211225','20220928','20221001','20230101']:
  c,_=available(TARGET,day)
  if c:out.append(('wayback_closest_'+day,c))
 out += [('live_http',TARGET),('live_https',TARGET.replace('http://','https://',1)),('live_http_slash',TARGET+'/'),('live_https_slash',TARGET.replace('http://','https://',1)+'/')]
 return list(dict.fromkeys(out))

def parse_jsonish(text):
 s=text.strip().lstrip('\ufeff')
 candidates=[s]
 m=re.search(r'(?s)(\[[\s\S]*\]|\{[\s\S]*\})',s)
 if m and m.group(1)!=s:candidates.append(m.group(1))
 for c in candidates:
  try:return json.loads(c)
  except:pass
 return None

def flatten(obj,path='root'):
 rows=[]
 if isinstance(obj,dict):
  rows.append((path,obj))
  for k,v in obj.items():
   if isinstance(v,(dict,list)):rows.extend(flatten(v,path+'.'+str(k)))
 elif isinstance(obj,list):
  for i,v in enumerate(obj):
   if isinstance(v,(dict,list)):rows.extend(flatten(v,f'{path}[{i}]'))
 return rows

def scalar(d,*names):
 for n in names:
  for k,v in d.items():
   if str(k).lower()==n.lower() and not isinstance(v,(dict,list)):return str(v)
 return ''
def urls_from_obj(d):
 found=[]
 for k,v in d.items():
  if isinstance(v,str):
   lowk=str(k).lower();lowv=v.lower()
   if any(x in lowk for x in ['url','link','pic','pdf','img','path']) or '/img/' in lowv or re.search(r'\.(?:pdf|jpe?g|png)(?:\?|$)',lowv):
    if '/' in v or v.startswith(('http','//')):found.append((str(k),absurl(v)))
 return found

def media_probe(url):
 r={'media_url':url,'status':'','source':'','resolved_url':'','content_type':'','bytes':'','sha256':'','magic':'','error':''};errs=[]
 todo=[('wayback_exact',archive(url))]
 c,_=available(url,'20220928')
 if c:todo.append(('wayback_closest',c))
 todo += [('live_http',url),('live_https',url.replace('http://','https://',1))]
 for src,u in list(dict.fromkeys(todo)):
  try:
   b,final,h=get(u,'image/*,application/pdf,*/*;q=0.5',MAX_MEDIA,2);ct=h.get('content-type','').lower();magic='pdf' if b.startswith(b'%PDF-') else ('jpeg' if b[:3]==b'\xff\xd8\xff' else ('png' if b.startswith(b'\x89PNG\r\n\x1a\n') else ''))
   if magic or ct.startswith('image/') or 'pdf' in ct:
    r.update({'status':'verified','source':src,'resolved_url':final,'content_type':ct,'bytes':str(len(b)),'sha256':hashlib.sha256(b).hexdigest(),'magic':magic});return r
   errs.append(f'{u}: nonmedia {ct} {len(b)}')
  except Exception as e:errs.append(f'{u}: {type(e).__name__}: {e}')
 r['status']='unverified';r['error']=' | '.join(errs)[:2600];return r

def main():
 responses=[];chosen=None;chosen_text='';chosen_data=None
 for src,u in attempts():
  rr={'source':src,'request_url':u,'status':'','resolved_url':'','content_type':'','bytes':'','sha256':'','jsonish':'','known_edition_mentions':'','media_literals':'','excerpt':'','error':''}
  try:
   b,final,h=get(u,'application/json,text/javascript,text/plain,text/html,*/*',MAX,2);t=textdecode(b,h);data=parse_jsonish(t);ed=sorted(set(f'{m.group(2)}:{m.group(1)}' for m in EDITION_RE.finditer(t)));ml=sorted(set(absurl(x) for x in URL_RE.findall(t) if MEDIA_RE.search(absurl(x))))
   rr.update({'status':'recovered','resolved_url':final,'content_type':h.get('content-type',''),'bytes':str(len(b)),'sha256':hashlib.sha256(b).hexdigest(),'jsonish':'yes' if data is not None else 'no','known_edition_mentions':'|'.join(ed),'media_literals':'|'.join(ml[:80]),'excerpt':re.sub(r'\s+',' ',t)[:2000]})
   if chosen is None and (data is not None or ed or ml):chosen=rr;chosen_text=t;chosen_data=data
  except Exception as e:rr['status']='error';rr['error']=f'{type(e).__name__}: {e}'
  responses.append(rr);print('RESPONSE',src,rr['status'],rr['jsonish'],rr['known_edition_mentions'],rr['media_literals'][:180],flush=True)
 page_rows=[];media_urls={}
 if chosen_data is not None:
  for path,d in flatten(chosen_data):
   eid=scalar(d,'editionid','edition_id','id');pname=scalar(d,'pagename','page','ban','banname');title=scalar(d,'pagetitle','title','name');page=KNOWN.get(eid,'')
   pairs=urls_from_obj(d)
   route='';m=None
   for k,u in pairs:
    mm=EDITION_RE.search(u)
    if mm:route=u;m=mm;eid=eid or mm.group(1);page=page or mm.group(2).upper()
   if eid in KNOWN:page=KNOWN[eid]
   if page or eid in KNOWN or pairs:
    vals={k:u for k,u in pairs}
    page_rows.append({'path':path,'page':page,'edition_id':eid,'pagename':pname,'title':title,'route':route,'pagepic':next((u for k,u in pairs if 'pagepic' in k.lower() or ('pic' in k.lower() and 'mobile' not in k.lower())),''),'mobilepic':next((u for k,u in pairs if 'mobile' in k.lower()),''),'pagepdf':next((u for k,u in pairs if 'pdf' in k.lower()),''),'all_urls':'|'.join(u for _,u in pairs)})
    for _,u in pairs:
     if MEDIA_RE.search(u):media_urls[u]=1
 # Regex fallback for non-JSON JS response.
 for u in sorted(set(absurl(x) for x in URL_RE.findall(chosen_text) if MEDIA_RE.search(absurl(x)))):media_urls[u]=1
 media_rows=[media_probe(u) for u in media_urls]
 with RESP.open('w',newline='',encoding='utf-8') as f:
  fs=['source','request_url','status','resolved_url','content_type','bytes','sha256','jsonish','known_edition_mentions','media_literals','excerpt','error'];w=csv.DictWriter(f,fieldnames=fs);w.writeheader();w.writerows(responses)
 with PAGES.open('w',newline='',encoding='utf-8') as f:
  fs=['path','page','edition_id','pagename','title','route','pagepic','mobilepic','pagepdf','all_urls'];w=csv.DictWriter(f,fieldnames=fs);w.writeheader();w.writerows(page_rows)
 with MEDIA.open('w',newline='',encoding='utf-8') as f:
  fs=['media_url','status','source','resolved_url','content_type','bytes','sha256','magic','error'];w=csv.DictWriter(f,fieldnames=fs);w.writeheader();w.writerows(media_rows)
 pages=sorted(set(r['page'] for r in page_rows if r['page']))
 report={'target':TARGET,'root_contract':'var allpagefile=webd+"api/Pagelist/48"','responses_recovered':sum(r['status']=='recovered' for r in responses),'usable_response_found':chosen is not None,'normalized_rows':len(page_rows),'pages_identified':pages,'complete_a1_to_a8':pages==[f'A{i}' for i in range(1,9)],'unique_media_urls':len(media_rows),'verified_media':sum(r['status']=='verified' for r in media_rows),'verified_pdfs':sum(r['status']=='verified' and r['magic']=='pdf' for r in media_rows),'notes':['API path is directly evidenced by verified Qilu Shaonian root HTML.','No response bodies or media bytes are committed; normalized metadata/hashes only.']}
 REPORT.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8');print(json.dumps(report,ensure_ascii=False,indent=2),flush=True)
if __name__=='__main__':main()
