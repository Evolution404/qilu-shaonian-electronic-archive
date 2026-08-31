#!/usr/bin/env python3
"""Inspect public/free 53BK v6.x distribution pages for digital-paper path conventions.

This is reference-software research only: archives are downloaded transiently when a
public direct download can be discovered, filenames/text snippets are inspected, and the
software archive itself is never committed. The goal is to recover generic image/PDF/API
naming conventions useful for locating historical 《齐鲁少年》 assets.
"""
from __future__ import annotations

import hashlib
import io
import json
import re
import tempfile
import urllib.parse
import urllib.request
import zipfile
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'data'/'cms_reference'; OUT.mkdir(parents=True,exist_ok=True)
UA='Mozilla/5.0 qilu-shaonian-cms-reference/1.0'
TIMEOUT=30
PAGES=[
 'https://www.jb51.net/codes/304094.html',
 'https://www.onlinedown.net/soft/117759.htm',
 'https://www.itmop.com/downinfo/407724.html',
 'https://www.53bk.com/',
]
ARCHIVE_HINT=re.compile(r'(?i)(?:53bk|304094|117759|407724|down|download).{0,120}\.(?:zip|rar|7z)(?:\?[^\"\'<>\s]*)?')
HREF_RE=re.compile(r'(?is)href=[\"\']([^\"\']+)[\"\']')
URL_RE=re.compile(r'(?i)https?://[^\"\'<>\s]+')
INTEREST=re.compile(r'(?i)(?:pubmobile|slidedata|calendarstart|calendarshow|pagelist|dayslist|edition|paper|pdf|img|mobile|53bkconfig|readtitle)')
TEXT_EXT=re.compile(r'(?i)\.(?:js|cs|aspx|ascx|config|xml|txt|sql)$')


def get(url,max_bytes=50*1024*1024):
 r=urllib.request.Request(url,headers={'User-Agent':UA,'Accept':'*/*','Referer':urllib.parse.urljoin(url,'/')})
 with urllib.request.urlopen(r,timeout=TIMEOUT) as x:
  data=x.read(max_bytes+1)
  if len(data)>max_bytes: raise ValueError('too large')
  return data,x.geturl(),{k.lower():v for k,v in x.headers.items()}

def decode(b):
 for e in ('utf-8','gb18030'):
  try:
   t=b.decode(e,'replace')
   if t.count('\ufffd')<30:return t
  except Exception:pass
 return b.decode('latin1','replace')

def candidates(page,html):
 out=[]
 for h in HREF_RE.findall(html):
  u=urllib.parse.urljoin(page,h.replace('&amp;','&'))
  low=u.lower()
  if any(x in low for x in ('.zip','.rar','.7z','download','downurl','down.php','down.aspx','softdown','file/','files/')):out.append(u)
 for u in URL_RE.findall(html):
  if ARCHIVE_HINT.search(u):out.append(u)
 return list(dict.fromkeys(out))

def try_archive(url):
 result={'url':url,'resolved_url':'','content_type':'','length':'','sha256':'','archive_type':'','entry_count':0,'interesting_entries':[],'interesting_text':[],'error':''}
 try:
  data,final,h=get(url)
  result.update({'resolved_url':final,'content_type':h.get('content-type',''),'length':len(data),'sha256':hashlib.sha256(data).hexdigest()})
  if data[:2]==b'PK':
   result['archive_type']='zip'
   with zipfile.ZipFile(io.BytesIO(data)) as z:
    names=z.namelist();result['entry_count']=len(names)
    hits=[n for n in names if INTEREST.search(n)];result['interesting_entries']=hits[:500]
    for n in hits:
     if TEXT_EXT.search(n):
      try:
       raw=z.read(n)
       if len(raw)>2_000_000:continue
       text=decode(raw)
       # Persist only small snippets around path/API terms, not full third-party source files.
       snippets=[]
       for m in INTEREST.finditer(text):
        a=max(0,m.start()-180);b=min(len(text),m.end()+350);s=re.sub(r'\s+',' ',text[a:b])
        if s not in snippets:snippets.append(s)
        if len(snippets)>=8:break
       if snippets:result['interesting_text'].append({'entry':n,'snippets':snippets})
      except Exception:pass
  elif data.startswith(b'Rar!'):
   result['archive_type']='rar_uninspected'
  elif data[:6]==b'7z\xbc\xaf\x27\x1c':
   result['archive_type']='7z_uninspected'
  else:
   # Some download endpoints return another HTML hop.
   text=decode(data)
   result['archive_type']='html_or_other'
   result['followup_candidates']=candidates(final,text)[:100]
 except Exception as e:result['error']=f'{type(e).__name__}: {e}'
 return result

def main():
 page_results=[]; allcand=[]
 for page in PAGES:
  try:
   raw,final,h=get(page,8*1024*1024);text=decode(raw);cs=candidates(final,text)
   page_results.append({'page':page,'resolved':final,'candidate_count':len(cs),'candidates':cs[:200],'error':''});allcand+=cs
  except Exception as e:page_results.append({'page':page,'resolved':'','candidate_count':0,'candidates':[],'error':f'{type(e).__name__}: {e}'})
 # Keep the transient probing bounded and prioritize likely archives/download endpoints.
 uniq=list(dict.fromkeys(allcand))[:150]
 probed=[]; queue=uniq[:]
 seen=set()
 while queue and len(probed)<180:
  u=queue.pop(0)
  if u in seen:continue
  seen.add(u);r=try_archive(u);probed.append(r)
  for v in r.get('followup_candidates',[])[:20]:
   if v not in seen:queue.append(v)
 archives=[r for r in probed if r.get('archive_type') in {'zip','rar_uninspected','7z_uninspected'}]
 report={'pages':page_results,'probed_count':len(probed),'archive_hits':len(archives),'archives':archives[:20],'notes':['Reference software bytes are transient and never committed.','Only filenames and short API/path snippets are persisted to infer generic CMS conventions.']}
 (OUT/'report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
 print(json.dumps({'probed_count':len(probed),'archive_hits':len(archives),'archive_summaries':[{'url':r['url'],'type':r['archive_type'],'entries':r['entry_count'],'hits':len(r['interesting_entries'])} for r in archives[:10]]},ensure_ascii=False,indent=2),flush=True)
 return 0
if __name__=='__main__':raise SystemExit(main())
