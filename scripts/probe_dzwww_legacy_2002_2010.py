#!/usr/bin/env python3
"""Enumerate historical Dazhongwang 《齐鲁少年》 section URLs for 2002-2010.

The known 2001 electronic edition uses four directories (一版/二版/三版/四版) and
YYYYMMDD-prefixed .htm filenames. Search engines expose only a small subset, so this
uses Wayback CDX URL inventory rather than keyword search.
"""
from __future__ import annotations
import csv,json,re,time
from pathlib import Path
from urllib.parse import urlencode
import requests
from bs4 import BeautifulSoup

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'data'/'dzwww_2002_2010_probe';OUT.mkdir(parents=True,exist_ok=True)
S=requests.Session();S.headers.update({'User-Agent':'qilu-shaonian-dzwww-archive-probe/1.0'})
SECTIONS={1:'qilushaonianyiban',2:'qilushaonianerban',3:'qilushaoniansanban',4:'qilushaoniansiban'}
DATE_RE=re.compile(r'/(20\d{6})\d*\.s?html?$',re.I)
ISSUE_RE=re.compile(r'(?:第\s*)?(\d{3,4})\s*期')

def get(url,timeout=45,retries=4):
    err=''
    for i in range(retries):
        try:
            r=S.get(url,timeout=timeout);r.raise_for_status();return r.content,r.url,''
        except Exception as e:
            err=f'{type(e).__name__}: {e}';time.sleep(1+i*2)
    return b'',url,err

def cdx(section):
    target=f'www.dzwww.com/qilushaonian/{section}/*'
    params={'url':target,'output':'json','fl':'timestamp,original,statuscode,mimetype,digest','filter':'statuscode:200','collapse':'urlkey'}
    raw,resolved,err=get('https://web.archive.org/cdx/search/cdx?'+urlencode(params),timeout=60)
    if not raw:return [],err
    try:data=json.loads(raw.decode('utf-8','replace'))
    except Exception as e:return [],f'JSON: {e}'
    if not data or len(data)<2:return [],''
    hdr=data[0];return [dict(zip(hdr,row)) for row in data[1:]],''

def decode(body):
    for enc in ('gb18030','utf-8','big5'):
        try:return body.decode(enc)
        except:pass
    return body.decode('latin1','replace')

def main():
    inventory=[];errors=[]
    for page,section in SECTIONS.items():
        rows,err=cdx(section)
        if err:errors.append({'stage':'cdx','page':page,'url':section,'error':err})
        for r in rows:
            u=r.get('original','');m=DATE_RE.search(u)
            pub=''
            if m:
                ds=m.group(1);y=int(ds[:4])
                if 2000<=y<=2010:pub=f'{ds[:4]}-{ds[4:6]}-{ds[6:8]}'
            inventory.append({'page':page,'section':section,'publication_date_from_url':pub,**r})
    target=[r for r in inventory if r['publication_date_from_url'] and 2002<=int(r['publication_date_from_url'][:4])<=2010]
    # Deduplicate by page/original, keep earliest capture.
    uniq={}
    for r in sorted(target,key=lambda x:x.get('timestamp','')):uniq.setdefault((r['page'],r['original']),r)
    target=list(uniq.values())
    recovered=[]
    for r in target:
        au=f"https://web.archive.org/web/{r['timestamp']}id_/{r['original']}"
        body,resolved,err=get(au)
        title='';excerpt='';issues='';links=0;media=0
        if body:
            soup=BeautifulSoup(decode(body),'html.parser');title=soup.title.get_text(' ',strip=True) if soup.title else ''
            text=re.sub(r'\s+',' ',soup.get_text(' ',strip=True));excerpt=text[:1600]
            issues='|'.join(sorted(set(ISSUE_RE.findall(text)),key=int));links=len(soup.find_all('a'));media=len(soup.find_all('img'))
        recovered.append({**r,'archive_url':resolved if body else au,'recovered':'yes' if body else 'no','title':title,'issue_numbers':issues,'link_count':links,'media_count':media,'excerpt':excerpt,'error':err})
    def write(name,rows,fields):
        with (OUT/name).open('w',encoding='utf-8',newline='') as f:
            w=csv.DictWriter(f,fieldnames=fields,extrasaction='ignore');w.writeheader();w.writerows(rows)
    write('inventory.csv',inventory,['page','section','publication_date_from_url','timestamp','original','statuscode','mimetype','digest'])
    write('target_pages.csv',recovered,['page','section','publication_date_from_url','timestamp','original','archive_url','recovered','title','issue_numbers','link_count','media_count','excerpt','error'])
    write('errors.csv',errors,['stage','page','url','error'])
    years=sorted({r['publication_date_from_url'][:4] for r in recovered if r.get('recovered')=='yes'})
    report={'inventory_rows':len(inventory),'candidate_2002_2010_urls':len(target),'recovered_2002_2010_pages':sum(r['recovered']=='yes' for r in recovered),'years_with_recovered_pages':years,'pages_with_issue_numbers':sum(bool(r.get('issue_numbers')) for r in recovered),'distinct_issue_numbers':sorted({int(x) for r in recovered for x in r.get('issue_numbers','').split('|') if x.isdigit()}),'cdx_errors':len(errors),'notes':['URL dates are treated as publication/date-path evidence only when filename begins YYYYMMDD.','This independently tests whether the Dazhongwang four-page electronic-edition mirror continued after the verified 2001 records.']}
    (OUT/'report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(json.dumps(report,ensure_ascii=False,indent=2))
if __name__=='__main__':main()
