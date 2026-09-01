#!/usr/bin/env python3
"""Focused recovery of the deployed mobileslide data layer used by szb.cnssiot.cn.

The verified 2022 root snapshot names these real deployment resources:
- Content/themes/mobileslide/js/slidedata.js?t=20200930
- calendarshowblue.js / calendarstartblue.js / weixinshare2019.js
- Comja/pubmobile.js
- /jquery/readtitle (extensionless dynamic script endpoint)

Earlier recovery incorrectly treated same-timestamp 404 as the end of the path. This pass tries
exact timestamp, closest Wayback capture, queryless resource variants, live HTTP/HTTPS, then
extracts AJAX URLs, extensionless /jquery/* routes, Pagepic/Pagepdf fields, edition routes and
/Img/ media references. Only metadata/short contexts are committed.
"""
from __future__ import annotations
import csv, hashlib, html, json, re, time, urllib.parse, urllib.request
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'data'/'szb_mobileslide_recovery'; OUT.mkdir(parents=True,exist_ok=True)
RES=OUT/'resources.csv'; END=OUT/'endpoints.csv'; PROBE=OUT/'endpoint_probes.csv'; REPORT=OUT/'report.json'
ORIGIN='http://szb.cnssiot.cn/'
TS='20220928010853'; UA='qilu-shaonian-mobileslide-recovery/1.0 (+https://github.com/Evolution404/qilu-shaonian-electronic-archive)'
TIMEOUT=35; MAX=8*1024*1024
KNOWN=[
 'http://szb.cnssiot.cn/Content/themes/mobileslide/js/calendarshowblue.js',
 'http://szb.cnssiot.cn/Content/themes/mobileslide/js/slidedata.js?t=20200930',
 'http://szb.cnssiot.cn/Content/themes/mobileslide/js/weixinshare2019.js?t=20190409',
 'http://szb.cnssiot.cn/Content/themes/mobileslide/js/calendarstartblue.js',
 'http://szb.cnssiot.cn/Comja/pubmobile.js',
 'http://szb.cnssiot.cn/jquery/readtitle',
]
ED={'A1':'326','A2':'333','A3':'327','A4':'328','A5':'329','A6':'330','A7':'331','A8':'332'}
IMG_RE=re.compile(r'''(?i)(?:https?://szb\.cnssiot\.cn)?/?Img/[^"'<>\s\\]+?\.(?:pdf|jpe?g|png)(?:\?[^"'<>\s\\]*)?''')
NOEXT_RE=re.compile(r'''(?i)(?:https?://szb\.cnssiot\.cn)?/?(?:jquery|ajax|api)/[a-z0-9_.-]+(?:\?[^"'<>\s)]*)?''')
HANDLER_RE=re.compile(r'''(?i)(?:https?://szb\.cnssiot\.cn)?/?[^"'<>\s]+\.(?:aspx|ashx)(?:\?[^"'<>\s)]*)?''')
FIELD_RE=re.compile(r'(?i)\b(Pagepic|Pagepdf|mobilepath|pcpath|editionid|Pagelink|Pagename|Pagetitle|qiid|Qiid)\b')
EDITION_RE=re.compile(r'(?i)content/2021-12/25/edition(\d+)_([A-Z]\d+)\.html')
AJAX_RE=re.compile(r'''(?is)(?:\$\.ajax|\$\.getJSON|\$\.get|\$\.post)\s*\([^)]{0,2200}\)''')
URL_RE=re.compile(r'''(?is)\burl\s*:\s*["']([^"']+)["']''')
DATA_RE=re.compile(r'''(?is)\bdata\s*:\s*(?:\{([^}]{0,1400})\}|["']([^"']+)["'])''')


def get(url,accept='*/*',limit=MAX,retries=2):
 last=None
 for a in range(retries):
  try:
   req=urllib.request.Request(url,headers={'User-Agent':UA,'Accept':accept})
   with urllib.request.urlopen(req,timeout=TIMEOUT) as r:
    b=r.read(limit+1)
    if len(b)>limit: raise ValueError('too large')
    return b,r.geturl(),{k.lower():v for k,v in r.headers.items()}
  except Exception as e:last=e;time.sleep(1+a*2)
 raise last

def decode(b,h):
 ct=h.get('content-type','');m=re.search(r'charset=([\w.-]+)',ct,re.I)
 best=None
 for enc in (([m.group(1)] if m else [])+['utf-8','gb18030']):
  try:
   t=b.decode(enc,'replace');q=t.count('\ufffd')
   if best is None or q<best[0]:best=(q,t)
  except:pass
 return best[1] if best else b.decode('utf-8','replace')

def archive(u,ts=TS):return f'https://web.archive.org/web/{ts}id_/{u}'
def available(u,day='20220928'):
 try:
  api='https://archive.org/wayback/available?'+urllib.parse.urlencode({'url':u,'timestamp':day})
  b,_,_=get(api,'application/json',2*1024*1024,2);d=json.loads(b.decode('utf-8','replace'));c=(d.get('archived_snapshots') or {}).get('closest') or {}
  if c.get('available') and c.get('url'):
   return re.sub(r'/web/(\d+)/',r'/web/\1id_/',c['url'],count=1),c.get('timestamp','')
 except:pass
 return '',''
def originize(base,v):
 v=html.unescape(v).strip().strip('"\'')
 if v.startswith('//'):v='http:'+v
 u=urllib.parse.urljoin(base,v);p=urllib.parse.urlsplit(u)
 if p.hostname and p.hostname.lower()!='szb.cnssiot.cn':return ''
 return urllib.parse.urlunsplit(('http','szb.cnssiot.cn',p.path,p.query,''))
def ctx(t,s,e):return re.sub(r'\s+',' ',t[max(0,s-700):min(len(t),e+900)]).strip()[:1900]

def resource_attempts(u):
 out=[('wayback_exact',archive(u))]
 p=urllib.parse.urlsplit(u)
 if p.query:
  noq=urllib.parse.urlunsplit((p.scheme,p.netloc,p.path,'',''))
  out.append(('wayback_queryless_exact',archive(noq)))
 else:noq=u
 for target in dict.fromkeys([u,noq]):
  for day in ['20211225','20220928','20230101']:
   c,_=available(target,day)
   if c:out.append(('wayback_closest_'+day,c))
 out += [('live_http',u),('live_https',u.replace('http://','https://',1))]
 return list(dict.fromkeys(out))

def extract(t,source):
 rows=[]
 for regex,kind in [(NOEXT_RE,'extensionless'),(HANDLER_RE,'handler')]:
  for m in regex.finditer(t):
   ep=originize(source,m.group(0))
   if ep:rows.append({'endpoint_url':ep,'kind':kind,'source_url':source,'params':'','context':ctx(t,m.start(),m.end())})
 for a in AJAX_RE.finditer(t):
  block=a.group(0);um=URL_RE.search(block)
  if not um:continue
  ep=originize(source,um.group(1))
  if not ep:continue
  dm=DATA_RE.search(block);params=((dm.group(1) or dm.group(2) or '') if dm else '')
  rows.append({'endpoint_url':ep,'kind':'ajax','source_url':source,'params':re.sub(r'\s+',' ',params)[:800],'context':ctx(t,a.start(),a.end())})
 return rows

def probe_candidates(ep,row):
 p=urllib.parse.urlsplit(ep);base=urllib.parse.urlunsplit(('http','szb.cnssiot.cn',p.path,'',''));out=[ep,base]
 low=(ep+' '+row.get('context','')+' '+row.get('params','')).lower()
 # Only known IDs/date; no arbitrary enumeration.
 if '/jquery/readtitle' in low or any(x in low for x in ['edition','page','banmian']):
  for eid in ED.values():
   for k in ['editionid','id']:out.append(base+'?'+urllib.parse.urlencode({k:eid}))
  for k in ['date','d','day','qi']:out.append(base+'?'+urllib.parse.urlencode({k:'2021-12-25'}))
 return list(dict.fromkeys(out))[:24]

def probe(ep,row):
 results=[]
 for u in probe_candidates(ep,row):
  rec={'endpoint_url':ep,'probe_url':u,'status':'','source':'','resolved_url':'','content_type':'','bytes':'','sha256':'','fields':'','assets':'','editions':'','excerpt':'','error':''};errs=[]
  attempts=[('wayback_exact',archive(u))]
  c,_=available(u,'20220928')
  if c:attempts.append(('wayback_closest',c))
  attempts += [('live_http',u),('live_https',u.replace('http://','https://',1))]
  for src,cand in list(dict.fromkeys(attempts)):
   try:
    b,final,h=get(cand,'application/json,text/javascript,text/plain,text/html,*/*',MAX,2);t=decode(b,h);fields=sorted(set(FIELD_RE.findall(t)));assets=sorted(set(originize(ORIGIN,x) for x in IMG_RE.findall(t) if originize(ORIGIN,x)));eds=sorted(set(f'{m.group(2)}:{m.group(1)}' for m in EDITION_RE.finditer(t)));ct=h.get('content-type','')
    # preserve any meaningful dynamic/script response, especially readtitle, even without Pagepdf literal
    meaningful=bool(fields or assets or eds or '/jquery/readtitle' in ep.lower() or 'json' in ct.lower() or len(t)>80)
    if meaningful:
     rec.update({'status':'recovered','source':src,'resolved_url':final,'content_type':ct,'bytes':str(len(b)),'sha256':hashlib.sha256(b).hexdigest(),'fields':'|'.join(fields),'assets':'|'.join(assets[:50]),'editions':'|'.join(eds),'excerpt':re.sub(r'\s+',' ',t)[:1800]});break
    errs.append(f'{cand}: no signal')
   except Exception as e:errs.append(f'{cand}: {type(e).__name__}: {e}')
  if rec['status']!='recovered':rec['status']='unverified';rec['error']=' | '.join(errs)[:2600]
  results.append(rec)
 return results

def main():
 resources=[];ends=[];probes=[]
 # Explicitly include the dynamic endpoint known from the verified deployment root.
 ends.append({'endpoint_url':'http://szb.cnssiot.cn/jquery/readtitle','kind':'root_script_src','source_url':ORIGIN,'params':'','context':'verified root snapshot script src=/jquery/readtitle'})
 for u in KNOWN:
  rec={'resource_url':u,'status':'','source':'','resolved_url':'','content_type':'','bytes':'','sha256':'','fields':'','assets':'','endpoints':'','excerpt':'','error':''};errs=[]
  for source,cand in resource_attempts(u):
   try:
    b,final,h=get(cand,'text/javascript,application/javascript,text/plain,text/html,*/*',MAX,2);t=decode(b,h);erows=extract(t,u);fields=sorted(set(FIELD_RE.findall(t)));assets=sorted(set(originize(ORIGIN,x) for x in IMG_RE.findall(t) if originize(ORIGIN,x)));eplist=sorted(set(r['endpoint_url'] for r in erows));
    # reject Wayback 404 wrapper pages masquerading as HTML
    if 'Wayback Machine has not archived that URL' in t or ('404 Not Found' in t and 'web.archive.org' in final):raise ValueError('archive 404 wrapper')
    rec.update({'status':'recovered','source':source,'resolved_url':final,'content_type':h.get('content-type',''),'bytes':str(len(b)),'sha256':hashlib.sha256(b).hexdigest(),'fields':'|'.join(fields),'assets':'|'.join(assets[:50]),'endpoints':'|'.join(eplist),'excerpt':re.sub(r'\s+',' ',t)[:2200]});ends.extend(erows);break
   except Exception as e:errs.append(f'{cand}: {type(e).__name__}: {e}')
  if rec['status']!='recovered':rec['status']='unrecovered';rec['error']=' | '.join(errs)[:3000]
  resources.append(rec);print('RESOURCE',u,rec['status'],rec['source'],rec['fields'],rec['assets'][:120],flush=True)
 # dedup endpoint, prefer ajax context
 d={}
 for r in ends:
  k=r['endpoint_url'];old=d.get(k)
  if old is None or (old['kind']!='ajax' and r['kind']=='ajax'):d[k]=r
 ends=sorted(d.values(),key=lambda r:r['endpoint_url'])
 for r in ends:
  probes.extend(probe(r['endpoint_url'],r))
  hit=sum(p['status']=='recovered' for p in probes if p['endpoint_url']==r['endpoint_url']);print('ENDPOINT',r['endpoint_url'],'hits',hit,flush=True)
 with RES.open('w',newline='',encoding='utf-8') as f:
  fs=['resource_url','status','source','resolved_url','content_type','bytes','sha256','fields','assets','endpoints','excerpt','error'];w=csv.DictWriter(f,fieldnames=fs);w.writeheader();w.writerows(resources)
 with END.open('w',newline='',encoding='utf-8') as f:
  fs=['endpoint_url','kind','source_url','params','context'];w=csv.DictWriter(f,fieldnames=fs);w.writeheader();w.writerows(ends)
 with PROBE.open('w',newline='',encoding='utf-8') as f:
  fs=['endpoint_url','probe_url','status','source','resolved_url','content_type','bytes','sha256','fields','assets','editions','excerpt','error'];w=csv.DictWriter(f,fieldnames=fs);w.writeheader();w.writerows(probes)
 report={'known_resources':len(KNOWN),'resources_recovered':sum(r['status']=='recovered' for r in resources),'resources_with_assets':sum(bool(r['assets']) for r in resources),'endpoints_discovered':len(ends),'endpoint_probes':len(probes),'endpoint_responses_recovered':sum(r['status']=='recovered' for r in probes),'responses_with_assets':sum(bool(r['assets']) for r in probes),'responses_with_page_fields':sum(bool(r['fields']) for r in probes),'notes':['Actual mobileslide deployment resources from verified root snapshot only.','Queryless and closest-capture recovery is attempted for cache-busted JS URLs.','No newspaper/image bytes are committed.']}
 REPORT.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8');print(json.dumps(report,ensure_ascii=False,indent=2),flush=True)
if __name__=='__main__':main()
