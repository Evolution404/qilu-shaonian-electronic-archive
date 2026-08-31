#!/usr/bin/env python3
"""Extract compact 53BK page-image/PDF field semantics for archival URL reconstruction.

Downloads the same public 53BK v6.x reference package transiently. No package/source bytes
are committed; only short contexts around page asset fields and relevant schema fragments.
"""
from __future__ import annotations

import io
import json
import re
import urllib.parse
import urllib.request
import zipfile
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'data'/'cms_reference';OUT.mkdir(parents=True,exist_ok=True)
PAGE='https://www.onlinedown.net/soft/117759.htm'
UA='Mozilla/5.0 qilu-shaonian-53bk-asset-logic/1.0'
HREF=re.compile(r'''(?is)(?:href|src)=["']([^"']+)["']''')
TERMS=re.compile(r'(?i)(Pagepic|Pagepdf|pagepath|PdfProcess|Pdfadd|PdfText|Upqipdf|editionimg|e_eachedition|e_scoedition|Img/|Img\\|\.pdf)')
TARGET_NAME=re.compile(r'(?i)(?:tables\.sql|constraints\.sql|editionadd|editionedit|pdfadd|pdfedit|pdfprocess|upqipdf|eachqi\.aspx|edition\.aspx|view\.aspx|pubmobile\.js|pageswf\.js)$')


def get(url,limit=80*1024*1024):
 req=urllib.request.Request(url,headers={'User-Agent':UA,'Accept':'*/*','Referer':PAGE})
 with urllib.request.urlopen(req,timeout=45) as r:
  b=r.read(limit+1)
  if len(b)>limit:raise ValueError('too large')
  return b,r.geturl()
def dec(b):
 best=None
 for e in ('utf-8','gb18030','big5'):
  try:
   t=b.decode(e,'replace');q=t.count('\ufffd')
   if best is None or q<best[0]:best=(q,t)
  except:pass
 return best[1] if best else b.decode('latin1','replace')
def package():
 pr,final=get(PAGE,8*1024*1024);text=dec(pr).replace('&amp;','&');cs=[]
 for h in HREF.findall(text):
  u=urllib.parse.urljoin(final,h)
  if '117759' in u and ('download' in u.lower() or 'iopdfbhjl' in u.lower()):cs.append(u)
 cs += ['https://www.onlinedown.net/iopdfbhjl/117759?module=download&t=website','http://www.onlinedown.net/iopdfbhjl/117759?module=download&t=website']
 for u in dict.fromkeys(cs):
  try:
   b,r=get(u)
   if b[:2]==b'PK':return b,r
  except:pass
 raise RuntimeError('reference package unavailable')
def contexts(text,limit=30):
 out=[];seen=set()
 for m in TERMS.finditer(text):
  a=max(0,m.start()-320);b=min(len(text),m.end()+650);s=re.sub(r'\s+',' ',text[a:b]).strip()
  if s not in seen:seen.add(s);out.append({'token':m.group(0),'context':s[:1200]})
  if len(out)>=limit:break
 return out
def main():
 raw,ref=package();results=[]
 with zipfile.ZipFile(io.BytesIO(raw)) as z:
  for n in z.namelist():
   norm=n.replace('\\','/')
   base=norm.rsplit('/',1)[-1]
   if not TARGET_NAME.search(base):continue
   try:b=z.read(n)
   except:continue
   if len(b)>3_000_000:continue
   t=dec(b)
   if not TERMS.search(t):continue
   cs=contexts(t)
   if cs:results.append({'entry':n,'contexts':cs})
 # Explicitly summarize schema around e_eachedition if present.
 schema=[]
 for r in results:
  if r['entry'].lower().endswith('tables.sql'):
   for c in r['contexts']:
    if 'e_eachedition' in c['context'].lower() or 'pagepic' in c['context'].lower() or 'pagepdf' in c['context'].lower():schema.append(c)
 report={'reference_url':ref,'matched_files':len(results),'files':results,'schema_focus':schema[:20],'notes':['Generic 53BK reference semantics only; not proof of a specific historical szb.cnssiot.cn file existing.','Use only to generate candidate URL patterns that must be independently verified.']}
 (OUT/'asset_logic.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
 print(json.dumps({'matched_files':len(results),'schema_contexts':len(schema),'entries':[r['entry'] for r in results]},ensure_ascii=False,indent=2),flush=True)
 return 0
if __name__=='__main__':raise SystemExit(main())
