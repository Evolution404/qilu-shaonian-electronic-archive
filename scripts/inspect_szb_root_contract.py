#!/usr/bin/env python3
"""Inspect hidden attributes/inline variables around the verified szb.cnssiot.cn root snapshot.

The root snapshot itself is recoverable even though most linked JS files are not. This extracts
short, non-copyright-sensitive markup contexts around all edition routes, /Img/ references,
readtitle, zoom/pdf/original/download terms, and every attribute on elements that contain the
known A1 media or edition links. It is intended to catch data-* / zoom / original-image paths
that simple src/href regexes may miss.
"""
from __future__ import annotations
import csv, hashlib, json, re, urllib.request
from html.parser import HTMLParser
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'data'/'szb_root_contract'; OUT.mkdir(parents=True,exist_ok=True)
CTX=OUT/'contexts.csv'; ELEM=OUT/'elements.csv'; REPORT=OUT/'report.json'
URL='https://web.archive.org/web/20220928010853id_/http://szb.cnssiot.cn/'
UA='qilu-shaonian-root-contract/1.0'
TOKENS=['mobile202112245170816f8aaf438f9a1a9119831a2eab','edition326_A1','edition333_A2','edition327_A3','edition328_A4','edition329_A5','edition330_A6','edition331_A7','edition332_A8','/jquery/readtitle','pagepic','pagepdf','pdf','zoom','original','large','download','editionid']
MEDIA_RE=re.compile(r'''(?i)(?:https?:)?//[^"'<>\s]+\.(?:pdf|jpe?g|png)(?:\?[^"'<>\s]*)?|/?(?:Img|img)/[^"'<>\s]+''')

class P(HTMLParser):
 def __init__(self):super().__init__(convert_charrefs=True);self.rows=[]
 def handle_starttag(self,tag,attrs):
  d={k:(v or '') for k,v in attrs}; blob=' '.join([tag]+[f'{k}={v}' for k,v in attrs])
  low=blob.lower()
  if any(t.lower() in low for t in TOKENS) or any(x in low for x in ['data-','zoom','pdf','original','large']):
   self.rows.append({'tag':tag,'id':d.get('id',''),'class':d.get('class',''),'attrs':' | '.join(f'{k}={v}' for k,v in attrs)[:3000]})

def main():
 req=urllib.request.Request(URL,headers={'User-Agent':UA,'Accept':'text/html,*/*'})
 with urllib.request.urlopen(req,timeout=40) as r:b=r.read(3*1024*1024);ct=r.headers.get('content-type','')
 text=b.decode('gb18030','replace')
 rows=[];seen=set()
 for tok in TOKENS:
  for m in re.finditer(re.escape(tok),text,re.I):
   c=re.sub(r'\s+',' ',text[max(0,m.start()-900):min(len(text),m.end()+1200)]).strip()[:2600]
   key=(tok,c)
   if key not in seen:seen.add(key);rows.append({'token':tok,'offset':m.start(),'context':c})
 # Generic media literals not covered by token list.
 for m in MEDIA_RE.finditer(text):
  c=re.sub(r'\s+',' ',text[max(0,m.start()-500):min(len(text),m.end()+700)]).strip()[:1800]
  key=('media_literal',c)
  if key not in seen:seen.add(key);rows.append({'token':'media_literal','offset':m.start(),'context':c})
 p=P();p.feed(text)
 with CTX.open('w',newline='',encoding='utf-8') as f:w=csv.DictWriter(f,fieldnames=['token','offset','context']);w.writeheader();w.writerows(rows)
 with ELEM.open('w',newline='',encoding='utf-8') as f:w=csv.DictWriter(f,fieldnames=['tag','id','class','attrs']);w.writeheader();w.writerows(p.rows)
 report={'snapshot':URL,'bytes':len(b),'content_type':ct,'sha256':hashlib.sha256(b).hexdigest(),'contexts':len(rows),'interesting_elements':len(p.rows),'unique_media_literals':len(set(MEDIA_RE.findall(text))),'tokens_found':sorted(set(r['token'] for r in rows)),'notes':['Only short HTML contexts and element attributes are stored; full HTML body is not committed.','Used to detect hidden data-* high-resolution/PDF asset references.']}
 REPORT.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8');print(json.dumps(report,ensure_ascii=False,indent=2))
if __name__=='__main__':main()
