#!/usr/bin/env python3
"""Inspect the archived qlsn.com '快乐下载' section for historical e-paper files."""
from __future__ import annotations
import csv, json, re, time
from pathlib import Path
from urllib.parse import urljoin, urlsplit
import requests
from bs4 import BeautifulSoup

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'data'/'legacy_downloads'
OUT.mkdir(parents=True,exist_ok=True)
S=requests.Session(); S.headers.update({'User-Agent':'qilu-shaonian-archive-download-inspector/1.0'})
SEEDS=[
('list','https://web.archive.org/web/20070623224444id_/http://www.qlsn.com/downloads_list.asp'),
('list50','https://web.archive.org/web/20070625133455id_/http://www.qlsn.com/downloads_list.asp?show=50'),
('known125','https://web.archive.org/web/20070623224226id_/http://www.qlsn.com/downloads_view.asp?id=125'),
]
VIEW_RE=re.compile(r'downloads_view\.asp\?id=(\d+)',re.I)
FILE_RE=re.compile(r'(?i)\.(?:pdf|zip|rar|7z|docx?|xlsx?|pptx?|jpe?g|png|gif|txt)(?:\?|$)')

def fetch(url,timeout=40):
    for n in range(4):
        try:
            r=S.get(url,timeout=timeout); r.raise_for_status(); return r.content,r.url,''
        except Exception as e:
            err=f'{type(e).__name__}: {e}'; time.sleep(1+n*2)
    return b'',url,err

def decode(b):
    for enc in ('gb18030','utf-8','big5'):
        try:return b.decode(enc)
        except:pass
    return b.decode('latin1','replace')

def original_from_wayback(url):
    m=re.search(r'/web/\d+(?:id_|if_)?/(https?://.*)$',url)
    return m.group(1) if m else url

def archive_for(original, ts='20070625133455'):
    return f'https://web.archive.org/web/{ts}id_/{original}'

def main():
    page_rows=[]; attachment_candidates=[]; view_urls={}
    for label,url in SEEDS:
        body,resolved,err=fetch(url)
        text=decode(body) if body else ''
        soup=BeautifulSoup(text,'html.parser') if text else None
        plain=re.sub(r'\s+',' ',soup.get_text(' ',strip=True)) if soup else ''
        links=[]
        if soup:
            base=original_from_wayback(resolved)
            for a in soup.find_all('a',href=True):
                href=a['href'].strip(); full=urljoin(base,href); links.append(full)
                m=VIEW_RE.search(full)
                if m:view_urls[m.group(1)]=full
                if FILE_RE.search(full): attachment_candidates.append((label,'',a.get_text(' ',strip=True),full))
        page_rows.append({'kind':label,'source_url':url,'resolved_url':resolved,'title':soup.title.get_text(' ',strip=True) if soup and soup.title else '', 'bytes':len(body),'link_count':len(links),'excerpt':plain[:1600],'error':err})
    # Seed id 125 even if list parsing was incomplete.
    view_urls.setdefault('125','http://www.qlsn.com/downloads_view.asp?id=125')
    view_rows=[]
    for did,original in sorted(view_urls.items(), key=lambda x:int(x[0])):
        # If href was a Wayback URL, normalize to the original target first.
        original=original_from_wayback(original)
        candidates=[archive_for(original,'20070625133455'),archive_for(original,'20070623224226')]
        body=b''; resolved=''; err=''
        for u in candidates:
            body,resolved,err=fetch(u)
            if body: break
        text=decode(body) if body else ''
        soup=BeautifulSoup(text,'html.parser') if text else None
        plain=re.sub(r'\s+',' ',soup.get_text(' ',strip=True)) if soup else ''
        files=[]
        if soup:
            base=original_from_wayback(resolved)
            for a in soup.find_all('a',href=True):
                full=urljoin(base,a['href'].strip())
                if FILE_RE.search(full):
                    files.append(full); attachment_candidates.append(('view',did,a.get_text(' ',strip=True),full))
        view_rows.append({'download_id':did,'original_url':original,'resolved_archive_url':resolved,'title':soup.title.get_text(' ',strip=True) if soup and soup.title else '', 'bytes':len(body),'file_refs':'|'.join(dict.fromkeys(files)),'excerpt':plain[:1800],'error':err})
    # Verify archive availability of file refs by direct 2007-era replay attempts.
    att_rows=[]
    seen=set()
    for parent,did,text,original in attachment_candidates:
        if original in seen: continue
        seen.add(original)
        body=b''; resolved=''; err=''
        for ts in ('20070625','20071225','20091225','20110918'):
            body,resolved,err=fetch(archive_for(original,ts),timeout=30)
            if body: break
        att_rows.append({'parent':parent,'download_id':did,'anchor_text':text,'original_url':original,'archive_recovered':'yes' if body else 'no','resolved_archive_url':resolved if body else '', 'bytes':len(body),'magic':body[:12].hex() if body else '', 'error':err if not body else ''})
    def write(name,rows,fields):
        with (OUT/name).open('w',encoding='utf-8',newline='') as f:
            w=csv.DictWriter(f,fieldnames=fields,extrasaction='ignore');w.writeheader();w.writerows(rows)
    write('pages.csv',page_rows,['kind','source_url','resolved_url','title','bytes','link_count','excerpt','error'])
    write('download_views.csv',view_rows,['download_id','original_url','resolved_archive_url','title','bytes','file_refs','excerpt','error'])
    write('attachments.csv',att_rows,['parent','download_id','anchor_text','original_url','archive_recovered','resolved_archive_url','bytes','magic','error'])
    report={'seed_pages':len(SEEDS),'download_view_ids':len(view_urls),'download_views_recovered':sum(not r['error'] and r['bytes'] for r in view_rows),'attachment_candidates':len(att_rows),'attachments_recovered':sum(r['archive_recovered']=='yes' for r in att_rows),'pdf_candidates':sum('.pdf' in r['original_url'].lower() for r in att_rows),'archive_recovered_pdf_candidates':sum('.pdf' in r['original_url'].lower() and r['archive_recovered']=='yes' for r in att_rows),'notes':['The download section is inspected because it is a plausible historical route for PDF/ZIP/page-image files.','No attachment is promoted as a newspaper issue unless content/title/issue evidence confirms it.']}
    (OUT/'report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(json.dumps(report,ensure_ascii=False,indent=2))
if __name__=='__main__':main()
