#!/usr/bin/env python3
"""Low-frequency Common Crawl recovery for historical szb.cnssiot.cn assets.

Previous broad crawls were rate-limited. This version serializes requests, retries 429/5xx,
and only queries indexes whose crawl dates overlap the verified CMS period (2020-2022).
"""
from __future__ import annotations
import csv,json,re,time,urllib.parse,urllib.request
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'data'/'szb_commoncrawl';OUT.mkdir(parents=True,exist_ok=True)
UA='qilu-shaonian-targeted-commoncrawl/1.0 (+https://github.com/Evolution404/qilu-shaonian-electronic-archive)'
TIMEOUT=40
TARGETS=['szb.cnssiot.cn/*','http://szb.cnssiot.cn/*','https://szb.cnssiot.cn/*']
YEAR_RE=re.compile(r'CC-MAIN-(20\d{2})-')
def get(url,retries=5):
 last=None
 for i in range(retries):
  try:
   req=urllib.request.Request(url,headers={'User-Agent':UA,'Accept':'application/json,text/plain,*/*'})
   with urllib.request.urlopen(req,timeout=TIMEOUT) as r:return r.read()
  except Exception as e:
   last=e;time.sleep(min(30,3*(i+1)))
 raise last
def indexes():
 data=json.loads(get('https://index.commoncrawl.org/collinfo.json').decode())
 out=[]
 for x in data:
  m=YEAR_RE.search(x.get('id',''))
  if m and 2020<=int(m.group(1))<=2022 and x.get('cdx-api'):out.append(x)
 return out
def main():
 idx=indexes();rows=[];errors=[]
 for i,x in enumerate(idx,1):
  for target in TARGETS:
   url=x['cdx-api']+'?'+urllib.parse.urlencode({'url':target,'output':'json','filter':'status:200'})
   try:
    text=get(url).decode('utf-8','replace').strip();n=0
    for line in text.splitlines():
     try:d=json.loads(line)
     except:continue
     u=d.get('url','');mime=d.get('mime',d.get('mime-detected',''))
     rows.append({'index':x['id'],'timestamp':d.get('timestamp',''),'url':u,'status':d.get('status',''),'mime':mime,'digest':d.get('digest',''),'filename':d.get('filename',''),'offset':d.get('offset',''),'length':d.get('length',''),'query_target':target});n+=1
    print(i,x['id'],target,'rows',n,flush=True)
   except Exception as e:
    errors.append({'index':x['id'],'target':target,'error':f'{type(e).__name__}: {e}'});print('ERROR',x['id'],target,e,flush=True)
   time.sleep(2)
 uniq={(r['url'],r['timestamp'],r['digest']):r for r in rows};rows=sorted(uniq.values(),key=lambda r:(r['timestamp'],r['url']))
 fields=['index','timestamp','url','status','mime','digest','filename','offset','length','query_target']
 with (OUT/'urls.csv').open('w',newline='',encoding='utf-8') as f:w=csv.DictWriter(f,fieldnames=fields);w.writeheader();w.writerows(rows)
 def typ(r):
  u=r['url'].lower();m=r['mime'].lower()
  if 'pdf' in m or '.pdf' in u:return 'pdf'
  if m.startswith('image/') or re.search(r'\.(?:jpe?g|png|tiff?|webp)(?:\?|$)',u):return 'image'
  if '/content/' in u:return 'content'
  if '/api/' in u or '/jquery/' in u:return 'api'
  return 'other'
 counts={}
 for r in rows:counts[typ(r)]=counts.get(typ(r),0)+1
 rep={'indexes_queried':len(idx),'unique_records':len(rows),'type_counts':counts,'errors':errors,'notes':['Common Crawl capture timestamp is not a publication date.','WARC filename/offset/length can be used later to retrieve a public captured response if a relevant page asset is found.']}
 (OUT/'report.json').write_text(json.dumps(rep,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
 print(json.dumps(rep,ensure_ascii=False,indent=2),flush=True)
 return 0
if __name__=='__main__':raise SystemExit(main())
