#!/usr/bin/env python3
"""Inspect independently archived qlsn.com media likely to belong to the 2007-2010 era.

Unlike the earlier media pass, this starts from the full Wayback URL inventory rather
than only images referenced by successfully recovered HTML. This can recover a scan
whose parent page was never archived. Newspaper bytes are transient; only metadata,
hashes, geometry, and OCR excerpts are committed.
"""
from __future__ import annotations

import csv, hashlib, io, json, re, subprocess, tempfile, time
from pathlib import Path
from urllib.parse import urlparse
import requests
from PIL import Image

ROOT=Path(__file__).resolve().parents[1]
SRC=ROOT/'data'/'archive_crawl'/'wayback_urls.csv'
OUT=ROOT/'data'/'independent_2007_2010_wayback_media'; OUT.mkdir(parents=True,exist_ok=True)
S=requests.Session(); S.headers.update({'User-Agent':'qilu-shaonian-independent-media/1.0','Accept':'image/*,application/pdf,*/*'})
IMG_EXT=re.compile(r'(?i)\.(?:jpe?g|png|gif|bmp|tiff?)(?:\?|$)')
PDF_EXT=re.compile(r'(?i)\.pdf(?:\?|$)')
KEYS=['齐鲁少年','齐 鲁 少 年','第','期','一版','二版','三版','四版','山东省少工委','少先队','本报记者']
ISSUE_RE=re.compile(r'(?:第\s*)?(\d{3,4})\s*期')


def fetch(url):
    last=''
    for n in range(4):
        try:
            r=S.get(url,timeout=(15,45)); r.raise_for_status(); return r.content,r.url,r.headers.get('Content-Type',''),''
        except Exception as e:
            last=f'{type(e).__name__}: {e}'; time.sleep(1+n*2)
    return b'',url,'',last


def ocr(img):
    with tempfile.NamedTemporaryFile(suffix='.png') as f:
        im=img.convert('RGB')
        if max(im.size)>3000:
            scale=3000/max(im.size); im=im.resize((max(1,int(im.width*scale)),max(1,int(im.height*scale))))
        im.save(f.name)
        p=subprocess.run(['tesseract',f.name,'stdout','-l','chi_sim+eng','--psm','6'],stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,text=True,timeout=45)
        return re.sub(r'\s+',' ',p.stdout).strip()


def main():
    rows=list(csv.DictReader(SRC.open(encoding='utf-8-sig',newline='')))
    candidates=[]; seen=set()
    for r in rows:
        u=r.get('original',''); ts=r.get('timestamp',''); mt=(r.get('mimetype') or '').lower()
        host=(urlparse(u).hostname or '').lower()
        if host not in {'qlsn.com','www.qlsn.com'}: continue
        if not ts[:4].isdigit(): continue
        capture_year=int(ts[:4])
        # 2007-2011 captures cover the active legacy site and may include late captures
        # of 2010 assets. 2004-2005 tiny assets were already exhaustively inspected.
        if not (2007 <= capture_year <= 2011): continue
        if not (mt.startswith('image/') or mt=='application/pdf' or IMG_EXT.search(u) or PDF_EXT.search(u)): continue
        digest=r.get('digest','')
        key=digest or u
        if key in seen: continue
        seen.add(key)
        candidates.append(r)

    out=[]
    for i,r in enumerate(candidates,1):
        rec={
          'capture_timestamp':r.get('timestamp',''),'original':r.get('original',''),'archive_url':r.get('archive_url',''),
          'inventory_mimetype':r.get('mimetype',''),'digest':r.get('digest',''),'resolved_url':'','content_type':'','bytes':'','sha256':'',
          'width':'','height':'','format':'','aspect':'','large':'no','page_geometry':'no','ocr_hits':'','issue_numbers':'','ocr_excerpt':'','error':''
        }
        body,resolved,ctype,err=fetch(rec['archive_url'])
        if err: rec['error']=err
        else:
            rec['resolved_url']=resolved; rec['content_type']=ctype; rec['bytes']=str(len(body)); rec['sha256']=hashlib.sha256(body).hexdigest()
            if body.startswith(b'%PDF'):
                rec['format']='PDF'; rec['large']='yes'; rec['page_geometry']='yes'
            else:
                try:
                    img=Image.open(io.BytesIO(body)); img.load(); rec['format']=img.format or ''
                    rec['width']=str(img.width); rec['height']=str(img.height)
                    aspect=img.width/img.height if img.height else 0; rec['aspect']=f'{aspect:.3f}'
                    large=img.width>=700 and img.height>=700 and len(body)>=60_000
                    pagegeom=img.height>=900 and 0.40<=aspect<=0.90
                    rec['large']='yes' if large else 'no'; rec['page_geometry']='yes' if pagegeom else 'no'
                    if large or pagegeom:
                        text=ocr(img); rec['ocr_hits']='|'.join(k for k in KEYS if k in text)
                        rec['issue_numbers']='|'.join(sorted(set(ISSUE_RE.findall(text)),key=int)); rec['ocr_excerpt']=text[:1400]
                except Exception as e: rec['error']=f'image_parse:{type(e).__name__}: {e}'
        out.append(rec)
        print(f"{i}/{len(candidates)} {rec['original']} {rec['width']}x{rec['height']} page={rec['page_geometry']} hits={rec['ocr_hits']} issues={rec['issue_numbers']} err={rec['error'][:60]}",flush=True)

    fields=['capture_timestamp','original','archive_url','inventory_mimetype','digest','resolved_url','content_type','bytes','sha256','width','height','format','aspect','large','page_geometry','ocr_hits','issue_numbers','ocr_excerpt','error']
    with (OUT/'media.csv').open('w',encoding='utf-8',newline='') as f:
        w=csv.DictWriter(f,fieldnames=fields); w.writeheader(); w.writerows(out)
    useful=[x for x in out if x['format']=='PDF' or x['page_geometry']=='yes' or x['ocr_hits'] or x['issue_numbers']]
    report={
      'inventory_media_selected':len(candidates),'responses_ok':sum(not x['error'] for x in out),'pdf_magic_rows':sum(x['format']=='PDF' for x in out),
      'large_images':sum(x['large']=='yes' for x in out),'page_geometry_images':sum(x['page_geometry']=='yes' for x in out),
      'ocr_keyword_rows':sum(bool(x['ocr_hits']) for x in out),'ocr_issue_number_rows':sum(bool(x['issue_numbers']) for x in out),'candidate_rows':len(useful),
      'candidates':[{k:x[k] for k in ('capture_timestamp','original','archive_url','bytes','width','height','format','sha256','ocr_hits','issue_numbers','ocr_excerpt')} for x in useful[:80]],
      'notes':['Selection comes from the complete Wayback inventory, not parent-linked HTML media.','2007-2011 capture window is used to catch late captures of 2010-era assets; newspaper date is never inferred from capture date alone.','Bytes are transient and not committed.']
    }
    (OUT/'report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(json.dumps(report,ensure_ascii=False,indent=2))
if __name__=='__main__': main()
