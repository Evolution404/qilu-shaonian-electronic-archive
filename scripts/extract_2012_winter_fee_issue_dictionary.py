#!/usr/bin/env python3
"""Extract structured issue/title hints from the editor-blog 1002-1014 contributor-fee post.

This is a metadata lead generator, not a text mirror. It fetches the verified editor blog post
《齐鲁少年》1002-1014期外稿费（2011年12月至2012年2月）, isolates the article body,
and records only short lines/contexts that contain issue/page/fee-like structure. Full post
text is not committed.
"""
from __future__ import annotations

import csv, json, re, urllib.request
from pathlib import Path
from bs4 import BeautifulSoup

ROOT=Path(__file__).resolve().parents[1]
OUTDIR=ROOT/'data'/'repost_fullpage'/'sina_2012_winter_combined'/'fee_dictionary'; OUTDIR.mkdir(parents=True,exist_ok=True)
OUT=OUTDIR/'hints.csv'; REPORT=OUTDIR/'report.json'
URL='https://blog.sina.com.cn/s/blog_4c4fc7d901012sbb.html'
UA='Mozilla/5.0 qilu-shaonian-winter-fee-dictionary/1.0'
ISSUE_RE=re.compile(r'(?<!\d)(100[2-9]|101[0-4])(?!\d)')
PAGE_RE=re.compile(r'(?:第\s*)?([1-8一二三四五六七八A-DＡ-Ｄ])\s*[版页]')
MONEY_RE=re.compile(r'\d+(?:\.\d+)?\s*元')


def fetch():
    req=urllib.request.Request(URL,headers={'User-Agent':UA,'Accept':'text/html,*/*;q=0.7'})
    with urllib.request.urlopen(req,timeout=30) as r:b=r.read(7*1024*1024);ct=r.headers.get('content-type','')
    best=None
    for enc in ['utf-8','gb18030']:
        t=b.decode(enc,'replace'); score=t.count('\ufffd')
        if best is None or score<best[0]:best=(score,t)
    return best[1],len(b),ct

def body_text(text):
    soup=BeautifulSoup(text,'html.parser')
    node=(soup.select_one('#sina_keyword_ad_area2') or soup.select_one('.articalContent') or soup.select_one('.article-content') or soup.select_one('article'))
    if not node:
        # Fall back to the largest text-heavy div, but never commit the raw full body.
        divs=soup.find_all('div'); node=max(divs,key=lambda x:len(x.get_text(' ',strip=True)),default=soup)
    for x in node.find_all(['script','style','noscript']):x.decompose()
    return node.get_text('\n',strip=True)

def clean(s):return re.sub(r'\s+',' ',s).strip()

def main():
    html,bytes_,ctype=fetch(); text=body_text(html)
    rawlines=[clean(x) for x in text.splitlines() if clean(x)]
    rows=[]; seen=set()
    for i,line in enumerate(rawlines):
        issues=ISSUE_RE.findall(line); pages=PAGE_RE.findall(line); money=MONEY_RE.findall(line)
        # Fee tables sometimes put issue/title/author/amount in adjacent short lines; include a
        # compact one-line window, capped to avoid reproducing the article.
        if not (issues or pages or money or '稿费' in line or '期' in line):continue
        window=' ｜ '.join(rawlines[max(0,i-1):min(len(rawlines),i+2)])
        window=window[:420]
        key=(tuple(issues),tuple(pages),window)
        if key in seen:continue
        seen.add(key)
        rows.append({'line_no':str(i+1),'issue_hints':'|'.join(dict.fromkeys(issues)),'page_hints':'|'.join(dict.fromkeys(pages)),'money_hints':'|'.join(money),'context':window})
    fields=['line_no','issue_hints','page_hints','money_hints','context']
    with OUT.open('w',newline='',encoding='utf-8') as f:w=csv.DictWriter(f,fieldnames=fields);w.writeheader();w.writerows(rows)
    issue_counts={str(n):0 for n in range(1002,1015)}
    for r in rows:
        for x in r['issue_hints'].split('|'):
            if x:issue_counts[x]=issue_counts.get(x,0)+1
    report={'source_url':URL,'source_bytes':bytes_,'content_type':ctype,'body_chars':len(text),'body_lines':len(rawlines),'structured_hint_rows':len(rows),'issue_counts':issue_counts,'page_hint_rows':sum(bool(r['page_hints']) for r in rows),'money_hint_rows':sum(bool(r['money_hints']) for r in rows),'notes':['Only short metadata contexts are stored; the full blog post is not mirrored.','Issue/title/author clues are for reverse-search and require independent page-image verification.']}
    REPORT.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8'); print(json.dumps(report,ensure_ascii=False,indent=2),flush=True)
    for r in rows[:40]:print(r,flush=True)
if __name__=='__main__':main()
