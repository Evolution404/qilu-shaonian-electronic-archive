#!/usr/bin/env python3
"""Audit recovered Sina “今天我评报/评报” images for full newspaper-page evidence.

The editor blog repeatedly used review posts to discuss specific issues.  This script selects
one highest-resolution recovered image per SHA from those posts, OCRs full and header regions,
and records issue/date/newspaper-identity evidence.  Image bytes are transient only.
"""
from __future__ import annotations
import csv, io, json, os, re, subprocess, tempfile, urllib.request
from pathlib import Path
from PIL import Image, ImageEnhance, ImageOps

ROOT=Path(__file__).resolve().parents[1]
SRC=ROOT/'data'/'repost_fullpage'/'sina_inline_media.csv'
OUTDIR=ROOT/'data'/'repost_fullpage'/'sina_review_pages'; OUTDIR.mkdir(parents=True,exist_ok=True)
OUT=OUTDIR/'ocr.csv'; REPORT=OUTDIR/'report.json'
UA='Mozilla/5.0 qilu-shaonian-sina-review-pages/1.0'
TITLE_RE=re.compile(r'今天我评报|评报',re.I)
ISSUE_RE=re.compile(r'(?:第\s*)?([0-9０-９]{3,5})\s*期')
DATE_RE=re.compile(r'20(?:0[0-9]|1[0-9]|2[0-9])\s*年\s*[0-9０-９]{1,2}\s*月\s*[0-9０-９]{1,2}\s*日')


def n(v):
    try:return int(v or 0)
    except:return 0

def fetch(r):
    req=urllib.request.Request(r['media_url'],headers={'User-Agent':UA,'Referer':r['post_url'],'Accept':'image/*,*/*'})
    with urllib.request.urlopen(req,timeout=30) as x:return x.read(25*1024*1024)

def ocr(im,psm=11):
    path=None
    try:
        im=ImageOps.autocontrast(im.convert('L')); im=ImageEnhance.Contrast(im).enhance(1.25)
        if max(im.size)>1900:
            s=1900/max(im.size); im=im.resize((round(im.width*s),round(im.height*s)))
        with tempfile.NamedTemporaryFile(suffix='.png',delete=False) as f:path=f.name
        im.save(path,'PNG')
        p=subprocess.run(['tesseract',path,'stdout','-l','chi_sim+eng','--psm',str(psm)],stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,timeout=45,check=False)
        return re.sub(r'\s+',' ',p.stdout.replace('\x0c',' ')).strip(), '' if p.returncode==0 else p.stderr[-500:]
    except Exception as e:return '',f'{type(e).__name__}: {e}'
    finally:
        if path and os.path.exists(path):os.unlink(path)

def evid(text):
    low=text.lower().replace(' ','')
    hits=[]
    if '齐鲁少年' in text:hits.append('masthead')
    if 'qlsn.com' in low or 'qisn.com' in low or 'qlisn.com' in low or 'qlen.com' in low:hits.append('official_site')
    if '857087447' in low:hits.append('official_qq')
    if '少工委' in text or '少工' in text:hits.append('sponsor')
    if '山东' in text:hits.append('shandong')
    issues=list(dict.fromkeys(ISSUE_RE.findall(text)))
    dates=list(dict.fromkeys(DATE_RE.findall(text)))
    return hits,issues,dates

def main():
    rows=list(csv.DictReader(SRC.open(encoding='utf-8')))
    c=[r for r in rows if r.get('http_status')=='200' and r.get('is_placeholder')=='no' and r.get('sha256') and TITLE_RE.search(r.get('post_title',''))]
    by={}
    for r in c:
        area=n(r.get('width'))*n(r.get('height'))
        old=by.get(r['sha256'])
        if old is None or area>old[0]:by[r['sha256']]=(area,r)
    # Prefer high-resolution originals, but keep modest images if unique to a review post.
    selected=[x[1] for x in sorted(by.values(),key=lambda x:x[0],reverse=True)[:16]]
    out=[]
    for i,r in enumerate(selected,1):
        rec={'rank':str(i),'post_url':r['post_url'],'post_date':r.get('post_date',''),'post_title':r.get('post_title',''),'issue_hints':r.get('issue_hints',''),'media_url':r['media_url'],'sha256':r['sha256'],'width':r.get('width',''),'height':r.get('height',''),'full_chars':'0','header_chars':'0','evidence_hits':'','issue_matches':'','date_matches':'','ocr_excerpt':'','classification':'ocr_error','error':''}
        try:
            body=fetch(r)
            with Image.open(io.BytesIO(body)) as im:
                im=im.convert('RGB'); full,err1=ocr(im,11); header,err2=ocr(im.crop((0,0,im.width,round(im.height*.38))),6)
            text=header+' '+full; hits,issues,dates=evid(text)
            chars=len(re.sub(r'\s+','',text)); score=len(set(hits))+(2 if issues else 0)+(1 if dates else 0)+(1 if chars>=120 else 0)
            if score>=5 and (issues or r.get('issue_hints')):cls='strong_review_page_candidate'
            elif len(set(hits))>=3 and chars>=70:cls='newspaper_page_candidate'
            elif chars>=160:cls='dense_review_image'
            else:cls='low_evidence_image'
            rec.update({'full_chars':str(len(re.sub(r'\s+','',full))),'header_chars':str(len(re.sub(r'\s+','',header))),'evidence_hits':'|'.join(sorted(set(hits))),'issue_matches':'|'.join(issues),'date_matches':'|'.join(dates),'ocr_excerpt':text[:2600],'classification':cls,'error':' | '.join(x for x in (err1,err2) if x)})
        except Exception as e:rec['error']=f'{type(e).__name__}: {e}'
        out.append(rec); print(rec['classification'],rec['post_date'],rec['post_title'],rec['evidence_hits'],rec['issue_matches'],flush=True)
    fields=list(out[0].keys()) if out else ['rank']
    with OUT.open('w',newline='',encoding='utf-8') as f:w=csv.DictWriter(f,fieldnames=fields);w.writeheader();w.writerows(out)
    report={'review_media_rows':len(c),'unique_review_images':len(by),'ocr_selected':len(selected),'strong_review_page_candidates':sum(r['classification']=='strong_review_page_candidate' for r in out),'newspaper_page_candidates':sum(r['classification']=='newspaper_page_candidate' for r in out),'dense_review_images':sum(r['classification']=='dense_review_image' for r in out),'candidates':[{k:r[k] for k in ('post_date','post_title','issue_hints','media_url','sha256','width','height','evidence_hits','issue_matches','date_matches','classification','ocr_excerpt')} for r in out if r['classification']!='low_evidence_image'],'notes':['Review-post images are high-value leads because the editor blog explicitly ties many posts to issue numbers.','No image bytes are committed.']}
    REPORT.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8'); print(json.dumps(report,ensure_ascii=False,indent=2),flush=True)
if __name__=='__main__':main()
