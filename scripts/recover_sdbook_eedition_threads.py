#!/usr/bin/env python3
"""Recover official qlsn.com forum threads about electronic editions/sample papers/downloads.

This is deliberately narrow. Historical list pages are scanned for anchors whose titles
mention 电子版、样报、邮寄、下载、PDF、合刊 etc. Matching thread URLs are then replayed
from Wayback and parsed for editor replies, external links, and media/attachment references.
No third-party binaries are committed.
"""
from __future__ import annotations
import csv,json,re,time
from pathlib import Path
from urllib.parse import urljoin
import requests
from bs4 import BeautifulSoup

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'data'/'sdbook_eedition_threads';OUT.mkdir(parents=True,exist_ok=True)
S=requests.Session();S.headers.update({'User-Agent':'qilu-shaonian-eedition-thread-recovery/1.0'})
KEY_RE=re.compile(r'电子版|电子报|样报|邮寄|PDF|pdf|下载|合刊|报纸|订阅|扫描')
FILE_RE=re.compile(r'(?i)\.(?:pdf|zip|rar|7z|jpe?g|png|gif|docx?)(?:\?|$)')
LISTS=[
('201105_root','https://web.archive.org/web/20110529092430id_/http://www.qlsn.com/sdbook/index.asp?'),
('201109_root','https://web.archive.org/web/20110918052805id_/http://www.qlsn.com/sdbook/index.asp?forumid=1&action=list'),
('201109_p1','https://web.archive.org/web/20110918104834id_/http://www.qlsn.com/sdbook/index.asp?forumid=1&fn=0&page=1&action='),
('201109_p2','https://web.archive.org/web/20110918112837id_/http://www.qlsn.com/sdbook/index.asp?forumid=1&fn=0&page=2&action='),
('201109_p3','https://web.archive.org/web/20110918115201id_/http://www.qlsn.com/sdbook/index.asp?forumid=1&fn=0&page=3&action='),
('201109_p4','https://web.archive.org/web/20110918084350id_/http://www.qlsn.com/sdbook/index.asp?forumid=1&fn=0&page=4&action='),
('201109_p5','https://web.archive.org/web/20110918121616id_/http://www.qlsn.com/sdbook/index.asp?forumid=1&fn=0&page=5&action='),
]

def fetch(url,timeout=50):
    last=''
    variants=[url,url.replace('id_/','if_/'),url.replace('id_/','')]
    for v in variants:
        for n in range(3):
            try:
                r=S.get(v,timeout=timeout);r.raise_for_status();return r.content,r.url,''
            except Exception as e:
                last=f'{type(e).__name__}: {e}';time.sleep(1+n*2)
    return b'',url,last

def decode(b):
    for enc in ('gb18030','utf-8','big5'):
        try:return b.decode(enc)
        except:pass
    return b.decode('latin1','replace')

def original_from_replay(url):
    m=re.search(r'/web/\d+(?:id_|if_)?/(https?://.*)$',url)
    return m.group(1) if m else url

def replay(original,ts='20110918'):
    return f'https://web.archive.org/web/{ts}id_/{original}'

def main():
    list_rows=[];threads={}
    for label,u in LISTS:
        body,resolved,err=fetch(u)
        matches=0
        if body:
            soup=BeautifulSoup(decode(body),'html.parser');base=original_from_replay(resolved)
            for a in soup.find_all('a',href=True):
                title=re.sub(r'\s+',' ',a.get_text(' ',strip=True))
                if title and KEY_RE.search(title):
                    href=urljoin(base,a['href'].strip())
                    # Ignore navigation/action links; retain actual sdbook topic-like paths.
                    if '/sdbook/' in href.lower() or 'sdbook' in href.lower():
                        matches+=1;threads.setdefault(href,{'title':title,'discovered_from':label})
        list_rows.append({'label':label,'source_url':u,'resolved_url':resolved,'bytes':len(body),'matching_anchors':matches,'error':err})
    thread_rows=[];links=[]
    for original,meta in threads.items():
        # Strip a replay wrapper if urljoin retained one.
        original=original_from_replay(original)
        body=b'';resolved='';err=''
        for ts in ('20110918','20110529','20101231','20110115'):
            body,resolved,err=fetch(replay(original,ts))
            if body:break
        title=meta['title'];plain='';reply_markers=0;external=[];files=[]
        if body:
            soup=BeautifulSoup(decode(body),'html.parser')
            plain=re.sub(r'\s+',' ',soup.get_text(' ',strip=True))
            reply_markers=len(re.findall(r'回复|楼主|版主|管理员|妮子|海霞|苍天一笑',plain))
            base=original_from_replay(resolved)
            for a in soup.find_all('a',href=True):
                full=urljoin(base,a['href'].strip());txt=re.sub(r'\s+',' ',a.get_text(' ',strip=True))
                if FILE_RE.search(full):files.append(full)
                host=''
                try:host=requests.utils.urlparse(full).hostname or ''
                except:pass
                if host and 'qlsn.com' not in host and 'web.archive.org' not in host:
                    external.append(full);links.append({'thread_url':original,'anchor_text':txt,'url':full,'kind':'external'})
            for f in files:links.append({'thread_url':original,'anchor_text':'','url':f,'kind':'file'})
        thread_rows.append({'thread_url':original,'discovered_title':title,'discovered_from':meta['discovered_from'],'resolved_archive_url':resolved,'recovered':'yes' if body else 'no','bytes':len(body),'reply_markers':reply_markers,'external_links':'|'.join(dict.fromkeys(external)),'file_refs':'|'.join(dict.fromkeys(files)),'excerpt':plain[:5000],'error':err})
    def write(name,rows,fields):
        with (OUT/name).open('w',encoding='utf-8',newline='') as f:
            w=csv.DictWriter(f,fieldnames=fields,extrasaction='ignore');w.writeheader();w.writerows(rows)
    write('list_pages.csv',list_rows,['label','source_url','resolved_url','bytes','matching_anchors','error'])
    write('threads.csv',thread_rows,['thread_url','discovered_title','discovered_from','resolved_archive_url','recovered','bytes','reply_markers','external_links','file_refs','excerpt','error'])
    write('links.csv',links,['thread_url','anchor_text','url','kind'])
    report={'list_pages':len(LISTS),'list_pages_recovered':sum(bool(r['bytes']) for r in list_rows),'matching_thread_urls':len(threads),'threads_recovered':sum(r['recovered']=='yes' for r in thread_rows),'threads_with_file_refs':sum(bool(r['file_refs']) for r in thread_rows),'threads_with_external_links':sum(bool(r['external_links']) for r in thread_rows),'titles':[r['discovered_title'] for r in thread_rows],'notes':['A 2010 forum question about when an electronic edition would exist is useful only after reading replies; the question alone does not prove non-existence.','This pass targets retrieval/access discussions rather than issue-number mining.']}
    (OUT/'report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(json.dumps(report,ensure_ascii=False,indent=2))
if __name__=='__main__':main()
