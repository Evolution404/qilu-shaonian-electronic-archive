#!/usr/bin/env python3
"""Inspect only publicly exposed demo/protocol resources for the 2023 Yuesu CMS package.

The public 51Aspx listing states that this CMS inherits the 53BK digital-paper system lineage and
shows Comja/pubmobile.js in its file tree. We do not bypass login/points/download restrictions.
This script only:
- fetches the public listing HTML;
- extracts explicit public demo/related-site URLs already embedded in that page;
- probes public demo roots and /Comja/pubmobile.js if available;
- records JS hashes and short contexts around allpagefile/Pagelist/pagepic/mobilepath/editionimg.

This is protocol-reference evidence only, never Qilu Shaonian content evidence.
"""
from __future__ import annotations
import csv, hashlib, html, json, re, time, urllib.parse, urllib.request
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'data'/'cms_reference'/'yuesu_public_demo';OUT.mkdir(parents=True,exist_ok=True)
LINKS=OUT/'links.csv';RES=OUT/'resources.csv';CTX=OUT/'contexts.csv';REPORT=OUT/'report.json'
LISTING='https://www.51aspx.com/code/yuesuCMS'
UA='qilu-archive-yuesu-public-demo/1.0 (+https://github.com/Evolution404/qilu-shaonian-electronic-archive)'
TIMEOUT=30;MAX=5*1024*1024
TOKENS=['allpagefile','api/Pagelist','Pagelist','pagepic','mobilepath','pcpath','editionimg','cureditionid','readtitle']
HREF_RE=re.compile(r'''(?is)href\s*=\s*["']([^"']+)["']''')
URL_RE=re.compile(r'''(?i)https?://[^"'<>\s]+''')

def get(url,accept='*/*',limit=MAX,retries=2):
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

def decode(b,h):
 ct=h.get('content-type','');m=re.search(r'charset=([\w.-]+)',ct,re.I);best=None
 for enc in (([m.group(1)] if m else [])+['utf-8','gb18030']):
  try:
   t=b.decode(enc,'replace');q=t.count('\ufffd')
   if best is None or q<best[0]:best=(q,t)
  except:pass
 return best[1] if best else b.decode('utf-8','replace')

def main():
 linkrows=[];resources=[];contexts=[];errors=[]
 try:b,final,h=get(LISTING,'text/html,*/*');text=decode(b,h)
 except Exception as e:
  REPORT.write_text(json.dumps({'listing':LISTING,'error':f'{type(e).__name__}: {e}'},ensure_ascii=False,indent=2)+'\n',encoding='utf-8');return
 candidates=[]
 for raw in HREF_RE.findall(text)+URL_RE.findall(text):
  u=html.unescape(raw).strip();u=urllib.parse.urljoin(final,u);p=urllib.parse.urlsplit(u)
  if p.scheme not in ('http','https') or not p.hostname:continue
  host=p.hostname.lower()
  # Keep external links and links whose nearby markup explicitly mentions demo/免费浏览/演示/相关网址.
  pos=text.find(raw);near=re.sub(r'\s+',' ',text[max(0,pos-220):pos+len(raw)+220]) if pos>=0 else ''
  kind='external' if '51aspx.com' not in host else 'internal'
  is_demo=bool(re.search(r'免费浏览|演示|demo|相关网址',near,re.I))
  linkrows.append({'url':u,'host':host,'kind':kind,'demo_context':'yes' if is_demo else 'no','context':near[:700]})
  if kind=='external' and is_demo:candidates.append(u)
 # De-duplicate likely public demo roots.
 roots=[]
 for u in candidates:
  p=urllib.parse.urlsplit(u);root=urllib.parse.urlunsplit((p.scheme,p.netloc,'/','',''))
  if root not in roots:roots.append(root)
 for root in roots[:12]:
  for label,u in [('root',root),('pubmobile',urllib.parse.urljoin(root,'Comja/pubmobile.js')),('pubmobile_lower',urllib.parse.urljoin(root,'comja/pubmobile.js'))]:
   row={'root':root,'kind':label,'url':u,'status':'','resolved_url':'','content_type':'','bytes':'','sha256':'','token_hits':'','error':''}
   try:
    b,rf,rh=get(u,'text/javascript,text/html,text/plain,*/*');t=decode(b,rh);hits=[x for x in TOKENS if x.lower() in t.lower()]
    row.update({'status':'recovered','resolved_url':rf,'content_type':rh.get('content-type',''),'bytes':str(len(b)),'sha256':hashlib.sha256(b).hexdigest(),'token_hits':'|'.join(hits)})
    for token in hits:
     for m in list(re.finditer(re.escape(token),t,re.I))[:6]:
      contexts.append({'root':root,'resource_url':u,'token':token,'context':re.sub(r'\s+',' ',t[max(0,m.start()-650):min(len(t),m.end()+950)]).strip()[:1700]})
   except Exception as e:row['status']='error';row['error']=f'{type(e).__name__}: {e}'[:1000]
   resources.append(row)
 with LINKS.open('w',newline='',encoding='utf-8') as f:w=csv.DictWriter(f,fieldnames=['url','host','kind','demo_context','context']);w.writeheader();w.writerows(linkrows)
 with RES.open('w',newline='',encoding='utf-8') as f:w=csv.DictWriter(f,fieldnames=['root','kind','url','status','resolved_url','content_type','bytes','sha256','token_hits','error']);w.writeheader();w.writerows(resources)
 with CTX.open('w',newline='',encoding='utf-8') as f:w=csv.DictWriter(f,fieldnames=['root','resource_url','token','context']);w.writeheader();w.writerows(contexts)
 report={'listing':LISTING,'listing_bytes':len(b) if 'b' in locals() else 0,'external_demo_roots':roots,'resources_probed':len(resources),'pubmobile_recovered':sum(r['kind'].startswith('pubmobile') and r['status']=='recovered' for r in resources),'protocol_contexts':len(contexts),'notes':['No authenticated/paid/download-only endpoint is accessed.','This is generic lineage/protocol evidence, not Qilu Shaonian content evidence.']}
 REPORT.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8');print(json.dumps(report,ensure_ascii=False,indent=2))
if __name__=='__main__':main()
